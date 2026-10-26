"""Punto de entrada: `btcb2t daemon [--live] [--config RUTA] [--env RUTA]
[--data-dir RUTA] [--socket RUTA]` y `btcb2t [attach] [--data-dir RUTA]
[--socket RUTA]`.

`daemon` es el servidor: el único proceso que habla con los venues, tiene las
keys, el lock y el journal, sin pantalla y en primer plano (RF-30). Sin
subcomando (o con `attach`), la TUI: el cliente de ese daemon por su socket,
que lo enseña y opera todo a través de él sin leer config ni keys (RF-31,
D-66). Sin flags, config y `.env` en `~/.config/btcb2t` y journal y socket en
`~/.local/state/btcb2t` (XDG, RNF-20). Un solo daemon por directorio de datos
(RNF-17)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Mapping
from pathlib import Path

from .adapters.base import Depositor, MarketData, TokenPricer, Venue, WithdrawalFees
from .adapters.dryrun import DryRunVenue
from .adapters.neoxa import NeoxaVenue
from .adapters.nonkyc import NonKYCVenue
from .config import ConfigError, RateLimitConfig, Settings, VenueConfig, load_settings
from .core.bot import FeeToken
from .core.journal import Journal
from .core.models import VenueId
from .core.ratelimit import RateLimiter
from .core.secrets import Redactor
from .diaglog import setup_diagnostics
from .paths import AlreadyRunning, InstanceLock, private_dir, resolve_paths
from .services.account import AccountService
from .services.bot import BotService
from .services.control import SOCKET_NAME, socket_problem
from .services.daemon import run_daemon
from .services.deposits import DepositService
from .services.engine import Engine
from .services.faults import Faults
from .services.market import MarketHub
from .services.orders import OrderService
from .services.rebalance import RebalanceService
from .services.remote import DaemonGone, Hello, RemoteEngine, probe
from .services.results import ResultsService
from .services.wire import ClientView
from .ui.app import BTCB2App
from .ui.clipboard import system_copy


def limiter(rl: RateLimitConfig) -> RateLimiter:
    return RateLimiter(
        general=(rl.general_per_min, 60),
        order=(rl.orders_per_10s, 10) if rl.orders_per_10s else None,
        cancel=(rl.cancels_per_10s, 10) if rl.cancels_per_10s else None,
    )


CLIENT_LOG = "client.log"


def build_venues(settings: Settings) -> dict[VenueId, MarketData]:
    cfg = settings.config
    fallback = cfg.polling.rest_fallback_every_s
    nk, nx = cfg.venues["nk"], cfg.venues["nx"]
    creds = settings.credentials
    return {
        "nk": NonKYCVenue(
            nk.symbol,
            limiter(cfg.ratelimit["nk"]),
            maker_fee=nk.maker_fee,
            taker_fee=nk.taker_fee,
            credentials=creds.get("nk"),
            fallback_every_s=fallback,
            fee_token=nk.fee_token,
        ),
        "nx": NeoxaVenue(
            nx.symbol,
            limiter(cfg.ratelimit["nx"]),
            credentials=creds.get("nx"),
            private_every_s=cfg.polling.neoxa_private_every_s,
            fallback_every_s=fallback,
            fee_token=nx.fee_token,
        ),
    }


def trading_venues(
    settings: Settings, venues: dict[VenueId, MarketData], hub: MarketHub
) -> dict[VenueId, Venue]:
    """Venues con los que se opera: los que tienen keys. Dry-run: envueltos en
    `DryRunVenue`, con saldos reales y órdenes simuladas (D-16) y el MarketSpec
    del hub (D-18). Live: los reales."""
    tradable = {vid: v for vid, v in venues.items() if settings.can_trade(vid)}
    if settings.mode == "dry-run":
        return {
            vid: DryRunVenue(v, spec=lambda vid=vid: hub.state[vid].spec)
            for vid, v in tradable.items()
        }
    out: dict[VenueId, Venue] = {}
    for vid, venue in tradable.items():
        if not isinstance(venue, Venue):
            raise TypeError(f"{vid}: el adaptador no tiene parte privada")
        out[vid] = venue
    return out


def deposit_service(settings: Settings, venues: dict[VenueId, MarketData]) -> DepositService | None:
    """Direcciones de depósito (RF-28) con los adaptadores reales de los venues
    con keys, en cualquier modo: solo se leen. Sin keys, None."""
    readers = {
        vid: v
        for vid, v in venues.items()
        if vid in settings.credentials and isinstance(v, Depositor)
    }
    return DepositService(readers) if readers else None


def rebalance_service(
    settings: Settings, venues: dict[VenueId, MarketData]
) -> RebalanceService | None:
    """Comisiones de retiro para el plan de reequilibrio (RF-29), como los
    depósitos: adaptadores reales de los venues con keys. Sin keys, None."""
    readers = {
        vid: v
        for vid, v in venues.items()
        if vid in settings.credentials and isinstance(v, WithdrawalFees)
    }
    return RebalanceService(readers) if readers else None


def results_service(
    directory: Path, settings: Settings, venues: Mapping[VenueId, MarketData]
) -> ResultsService:
    """Resultados del journal de `directory` (RF-25), con el precio del token de
    fee de cada venue que lo tenga para valorar sus fees (D-65)."""
    pricers = {}
    for vid, v in settings.config.venues.items():
        venue = venues.get(vid)
        if v.fee_token is not None and isinstance(venue, TokenPricer):
            pricers[v.fee_token] = lambda venue=venue, token=v.fee_token: venue.token_price(token)
    return ResultsService(directory, pricers=pricers)


def fee_tokens(venues: Mapping[VenueId, VenueConfig]) -> dict[VenueId, FeeToken]:
    """Venues que pagan la fee en token, para el bot (RF-24, D-63)."""
    return {
        vid: FeeToken(v.fee_token, v.fee_token_min, v.fee_discount_pct)
        for vid, v in venues.items()
        if v.fee_token is not None and v.fee_token_min is not None
    }


def build_engine(
    settings: Settings,
    logs: Path = Path("logs"),
    venues: dict[VenueId, MarketData] | None = None,
) -> Engine:
    """Órdenes y cuenta comparten venues y journal, y las órdenes validan con lo
    que ya tiene la cuenta (D-18). En dry-run la cuenta lee los `DryRunVenue`
    (saldos reales, órdenes simuladas); en solo lectura no hay nada. Lo usan la
    terminal y el daemon (D-61)."""
    cfg = settings.config
    venues = venues if venues is not None else build_venues(settings)
    orders: OrderService | None = None
    account: AccountService | None = None
    bot: BotService | None = None
    journal: Journal | None = None
    if settings.mode == "read-only":
        hub = MarketHub(venues)
    else:
        journal = Journal(logs, settings.mode, settings.redactor)
        faults = Faults(journal)  # uno para todos: la barra cuenta los de la sesión
        hub = MarketHub(venues, faults=faults)
        trading = trading_venues(settings, venues, hub)
        every = cfg.polling.balances_every_s
        account = AccountService(trading, journal=journal, balances_every_s=every, faults=faults)
        live = settings.mode == "live"  # el desfase de reloj solo bloquea en live (D-30)
        orders = OrderService(
            hub, trading, journal, cfg.risk, account=account, faults=faults, live=live
        )
        if cfg.bot is not None:  # fase 2: el bot opera con las mismas piezas (D-36)
            bot = BotService(
                hub, orders, account, journal, cfg.bot, live=live, faults=faults,
                target=cfg.inventory, fee_tokens=fee_tokens(cfg.venues),
            )  # fmt: skip
    return Engine(
        hub, mode=settings.mode, orders=orders, account=account, bot=bot,
        max_clock_skew_s=cfg.risk.max_clock_skew_s, journal=journal,
        value_every_s=cfg.polling.value_every_s,
        # lo que el cliente pide por el socket (D-66)
        results=results_service(logs, settings, venues),
        deposits=deposit_service(settings, venues),
        rebalance=rebalance_service(settings, venues),
    )  # fmt: skip


def client_view(settings: Settings) -> ClientView:
    """Lo que la pantalla del cliente necesita de la config; viaja en `hello`."""
    cfg = settings.config
    return ClientView(
        book_levels=cfg.ui.book_levels,
        timeframe=cfg.ui.default_timeframe,
        fee_discounts={vid: v.fee_discount_pct for vid, v in cfg.venues.items()},
        inventory=cfg.inventory,
        bot_limits=cfg.bot.limits if cfg.bot is not None else None,
    )


def build_client_app(socket: Path, hello: Hello) -> BTCB2App:
    """El cliente: todo lo del daemon por el socket (RF-31, D-66). Ni config,
    ni keys, ni venues, ni journal, ni lock."""
    view = hello.view or DEFAULT_VIEW
    remote = RemoteEngine(socket, has_bot=hello.has_bot)
    hub = remote.market
    return BTCB2App(
        hub,
        run_hub=False,
        book_levels=view.book_levels,
        timeframe=view.timeframe,
        mode=hello.mode,
        account=remote.account,
        orders=remote.orders,
        bot=remote.bot,
        fee_discounts=view.fee_discounts,
        results=remote.results,
        deposits=remote.deposits,
        rebalance=remote.rebalance,
        inventory_target=view.inventory,
        clipboard=system_copy,  # OSC 52 no llega en GNOME Terminal (D-52)
        bot_limits=view.bot_limits,
        engine=Engine(hub, mode=hello.mode),  # type: ignore[arg-type]  # solo avisos
        remote=remote,
    )


DEFAULT_VIEW = ClientView(15, "1m", {}, None, None)  # un daemon que no manda su config


def client(data: Path, socket: Path) -> None:
    try:
        hello = asyncio.run(probe(socket))
    except DaemonGone as exc:
        raise SystemExit(f"No se puede conectar: {exc}.") from None
    private_dir(data)
    # el cliente no tiene keys que redactar
    diag = setup_diagnostics(data, Redactor([]), "INFO", name=CLIENT_LOG)
    logging.getLogger(__name__).info("cliente de %s (%s)", socket, hello.mode)
    try:
        build_client_app(socket, hello).run()
    finally:
        diag.close()
    print("Cliente desconectado: el daemon sigue en marcha.", file=sys.stderr)


async def serve(settings: Settings, data: Path, socket: Path) -> None:
    """El daemon: SIGTERM y SIGINT lo paran en orden; una línea por stderr al
    quedar listo, para quien lo supervise (D-61)."""
    engine = build_engine(settings, logs=data)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    for warning in settings.warnings:
        logging.getLogger(__name__).warning("%s", warning)
        print(f"aviso: {warning}", file=sys.stderr)

    def ready() -> None:
        print(f"btcb2t daemon en {settings.mode}, datos en {data}", file=sys.stderr)

    await run_daemon(
        engine, stop, on_ready=ready, socket=socket,
        book_depth=settings.config.ui.book_levels, view=client_view(settings),
    )  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(prog="btcb2t", description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("daemon", "attach"),
        help="daemon: el servidor, sin pantalla y en primer plano (para con SIGTERM); "
        "sin subcomando o attach: la TUI, cliente del daemon",
    )  # fmt: skip
    parser.add_argument("--config", help="config.toml del daemon (por defecto ~/.config/btcb2t/)")
    parser.add_argument("--env", help=".env del daemon (por defecto ~/.config/btcb2t/)")
    parser.add_argument(
        "--data-dir", help="journal, lock y socket (por defecto ~/.local/state/btcb2t)"
    )
    parser.add_argument("--live", action="store_true", help="daemon: enviar órdenes reales")
    parser.add_argument("--socket", help="socket de control (por defecto, en el de datos)")
    args = parser.parse_args()
    paths = resolve_paths(args.config, args.env, args.data_dir)
    socket = Path(args.socket).expanduser().absolute() if args.socket else paths.data / SOCKET_NAME
    if args.command != "daemon":
        if args.live:
            raise SystemExit(
                "El cliente no lleva --live: el modo es el del daemon (btcb2t daemon --live)."
            )
        client(paths.data, socket)
        return
    if not paths.config.exists():
        raise SystemExit(
            f"No existe {paths.config}: cópialo de config.example.toml y revísalo "
            "(o indica otro con --config)."
        )
    try:
        settings = load_settings(paths.config, paths.env, args.live)
    except ConfigError as exc:
        raise SystemExit(f"Configuración inválida: {exc}") from None
    if problem := socket_problem(socket):
        raise SystemExit(f"No se puede abrir el socket: {problem}.")
    lock = InstanceLock(paths.data)
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        raise SystemExit(str(exc)) from None
    diag = setup_diagnostics(paths.data, settings.redactor, settings.config.log.level)
    logging.getLogger(__name__).info("arranque en %s, datos en %s", settings.mode, paths.data)
    try:
        asyncio.run(serve(settings, paths.data, socket))
    finally:
        diag.close()
        lock.release()


if __name__ == "__main__":
    main()
