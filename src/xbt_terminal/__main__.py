"""Punto de entrada: `xbt daemon [--live] [--record] [--replay FICHERO]
[--config RUTA] [--env RUTA] [--data-dir RUTA] [--socket RUTA]`,
`xbt report --replay FICHERO [--config RUTA]` y `xbt [attach] [--data-dir RUTA]
[--socket RUTA]`.

`daemon` es el servidor: el único proceso que habla con los venues, tiene las
keys, el lock y el journal, sin pantalla y en primer plano (RF-30). Sin
subcomando (o con `attach`), la TUI: el cliente de ese daemon por su socket,
que lo enseña y opera todo a través de él sin leer config ni keys (RF-31,
D-66). Sin flags, config y `.env` en `~/.config/xbt` y journal y socket en
`~/.local/state/xbt` (XDG, RNF-20). Un solo daemon por directorio de datos
(RNF-17). `--record` graba el mercado, `--replay` sustituye los venues por una
grabación y `report` da el resultado del bot sobre ella (RF-33 a RF-35, D-80)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .adapters.base import Depositor, MarketData, TokenPricer, Venue, WithdrawalFees
from .adapters.dryrun import DryRunVenue
from .adapters.neoxa import NeoxaVenue
from .adapters.nonkyc import NonKYCVenue
from .adapters.streaming import now_ms
from .config import ConfigError, RateLimitConfig, Settings, VenueConfig, load_settings
from .core.bot import FeeToken
from .core.journal import Journal
from .core.models import VenueId
from .core.ratelimit import RateLimiter
from .core.replay import ReplayReport, render
from .core.secrets import Redactor
from .diaglog import setup_diagnostics
from .paths import AlreadyRunningError, InstanceLock, private_dir, resolve_paths
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
from .services.recording import (
    Recorder,
    RecordingError,
    RecordingInfo,
    recording_path,
    scan_recording,
)
from .services.remote import DaemonGoneError, Hello, RemoteEngine, probe
from .services.replay import (
    CrossingProbe,
    ReplayVenue,
    SimVenue,
    Timeline,
    VirtualLoop,
    run_report,
    summarize_journal,
)
from .services.results import ResultsService
from .services.wire import ClientView
from .ui.app import ClientServices, XbtApp
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


def replay_venues(
    recording: Path, info: RecordingInfo, clock: Callable[[], int] = now_ms
) -> dict[VenueId, MarketData]:
    """Los venues que salen de una grabación, con un solo origen de tiempos."""
    timeline = Timeline(info, clock, venues=len(info.specs))
    return {vid: ReplayVenue(vid, recording, info, timeline, clock) for vid in info.specs}


def trading_venues(
    settings: Settings,
    venues: dict[VenueId, MarketData],
    hub: MarketHub,
    clock: Callable[[], int] = now_ms,
) -> dict[VenueId, Venue]:
    """Venues con los que se opera: los que tienen keys. Dry-run: envueltos en
    `DryRunVenue`, con saldos reales y órdenes simuladas (D-16) y el MarketSpec
    del hub (D-18). Live: los reales. Reproducción: `SimVenue` sobre la grabación
    (RF-34)."""
    if settings.replay:
        cfg = settings.config.replay
        assert cfg is not None  # load_settings no deja llegar aquí sin ella
        return {
            vid: SimVenue(v, cfg, clock=clock)
            for vid, v in venues.items()
            if isinstance(v, ReplayVenue)
        }
    tradable = {vid: v for vid, v in venues.items() if settings.can_trade(vid)}
    if settings.mode == "dry-run":
        return {
            vid: DryRunVenue(v, spec=lambda vid=vid: hub.state[vid].spec, clock=clock)
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
    clock: Callable[[], int] = now_ms,
) -> Engine:
    """Órdenes y cuenta comparten venues y journal, y las órdenes validan con lo
    que ya tiene la cuenta (D-18). En dry-run la cuenta lee los `DryRunVenue`
    (saldos reales, órdenes simuladas); en solo lectura no hay nada. Lo usan la
    terminal y el daemon (D-61). `clock`: el del motor entero; el informe le pasa
    el tiempo virtual (D-80)."""
    cfg = settings.config
    venues = venues if venues is not None else build_venues(settings)
    orders: OrderService | None = None
    account: AccountService | None = None
    bot: BotService | None = None
    journal: Journal | None = None
    if settings.mode == "read-only":
        hub = MarketHub(venues)
    else:
        journal = Journal(
            logs, settings.mode, settings.redactor,
            clock=lambda: datetime.fromtimestamp(clock() / 1000, UTC),
        )  # fmt: skip
        # uno para todos: la barra cuenta los de la sesión
        faults = Faults(journal, clock=clock)
        hub = MarketHub(
            venues, faults=faults, journal=journal, book_check_s=cfg.risk.book_check_s,
            clock=clock,
        )  # fmt: skip
        trading = trading_venues(settings, venues, hub, clock)
        every = cfg.polling.balances_every_s
        account = AccountService(
            trading, journal=journal, balances_every_s=every, faults=faults, clock=clock
        )
        live = settings.mode == "live"  # el desfase de reloj solo bloquea en live (D-30)
        orders = OrderService(
            hub, trading, journal, cfg.risk, account=account, faults=faults, live=live,
            clock=clock,
        )  # fmt: skip
        if cfg.bot is not None:  # fase 2: el bot opera con las mismas piezas (D-36)
            bot = BotService(
                hub, orders, account, journal, cfg.bot, live=live, faults=faults,
                target=cfg.inventory, fee_tokens=fee_tokens(cfg.venues), clock=clock,
            )  # fmt: skip
    return Engine(
        hub, mode=settings.mode, orders=orders, account=account, bot=bot,
        max_clock_skew_s=cfg.risk.max_clock_skew_s, journal=journal,
        value_every_s=cfg.polling.value_every_s, clock=clock,
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


def build_client_app(socket: Path, hello: Hello) -> XbtApp:
    """El cliente: todo lo del daemon por el socket (RF-31, D-66). Ni config,
    ni keys, ni venues, ni journal, ni lock."""
    view = hello.view or DEFAULT_VIEW
    remote = RemoteEngine(socket, has_bot=hello.has_bot)
    hub = remote.market
    services = ClientServices(
        orders=remote.orders,
        account=remote.account,
        bot=remote.bot,
        results=remote.results,
        deposits=remote.deposits,
        rebalance=remote.rebalance,
        remote=remote,
    )
    return XbtApp(
        hub,
        view=view,
        mode=hello.mode,
        engine=Engine(hub, mode=hello.mode),  # type: ignore[arg-type]  # solo avisos
        services=services,
        run_hub=False,
        clipboard=system_copy,  # OSC 52 no llega en GNOME Terminal (D-52)
    )


DEFAULT_VIEW = ClientView(15, "1m", {}, None, None)  # un daemon que no manda su config


def client(data: Path, socket: Path) -> None:
    try:
        hello = asyncio.run(probe(socket))
    except DaemonGoneError as exc:
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


def replay_report(settings: Settings, recording: Path) -> ReplayReport:
    """El informe de RF-35: el motor entero sobre la grabación, en tiempo virtual
    y con un journal que se tira al acabar."""
    info = scan_recording(recording)
    bot = settings.config.bot
    if bot is None:
        raise ConfigError("[bot]: hace falta para el informe")
    loop = VirtualLoop(info.first / 1000)
    try:
        with tempfile.TemporaryDirectory(prefix="xbt-report-") as tmp:
            feeds = replay_venues(recording, info, loop.now_ms)
            engine = build_engine(settings, logs=Path(tmp), venues=feeds, clock=loop.now_ms)
            probe_ = CrossingProbe(engine.hub, bot.limits, loop.now_ms)
            timeline = next(v.timeline for v in feeds.values() if isinstance(v, ReplayVenue))
            loop.run_until_complete(run_report(engine, timeline))
            return summarize_journal(Path(tmp), probe_.count, info)
    finally:
        loop.close()


async def serve(
    settings: Settings,
    data: Path,
    socket: Path,
    *,
    record: bool = False,
    venues: dict[VenueId, MarketData] | None = None,
) -> None:
    """El daemon: SIGTERM y SIGINT lo paran en orden; una línea por stderr al
    quedar listo, para quien lo supervise (D-61)."""
    engine = build_engine(settings, logs=data, venues=venues)
    recorder = None
    if record:
        recorder = Recorder(engine.hub, recording_path(data, now_ms()))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    for warning in settings.warnings:
        logging.getLogger(__name__).warning("%s", warning)
        print(f"aviso: {warning}", file=sys.stderr)

    def ready() -> None:
        print(f"xbt daemon en {settings.mode}, datos en {data}", file=sys.stderr)

    try:
        await run_daemon(
            engine, stop, on_ready=ready, socket=socket,
            book_depth=settings.config.ui.book_levels, view=client_view(settings),
        )  # fmt: skip
    finally:
        if recorder is not None:
            recorder.close()


def replay_problem(args: argparse.Namespace) -> str | None:
    """Qué no se puede combinar con `--replay` (RF-34), o None."""
    if args.replay is None:
        return None
    if args.live:
        return "--replay no admite --live: una reproducción nunca envía órdenes reales."
    if args.record:
        return "--replay no admite --record: la grabación ya existe."
    if args.data_dir is None:
        return "--replay necesita --data-dir, para no mezclarse con el journal real."
    return None


def report(args: argparse.Namespace, config: Path, env: Path) -> None:
    """`xbt report`: el informe de RF-35 por stdout."""
    if args.replay is None:
        raise SystemExit("xbt report necesita --replay FICHERO.")
    try:
        settings = load_settings(config, env, False, replay=True)
        print(render(replay_report(settings, Path(args.replay).expanduser())))
    except (ConfigError, RecordingError) as exc:
        raise SystemExit(f"No se puede hacer el informe: {exc}") from None


def main() -> None:
    parser = argparse.ArgumentParser(prog="xbt", description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("daemon", "attach", "report"),
        help="daemon: el servidor, sin pantalla y en primer plano (para con SIGTERM); "
        "sin subcomando o attach: la TUI, cliente del daemon; "
        "report: resultado del bot sobre una grabación",
    )  # fmt: skip
    parser.add_argument("--config", help="config.toml del daemon (por defecto ~/.config/xbt/)")
    parser.add_argument("--env", help=".env del daemon (por defecto ~/.config/xbt/)")
    parser.add_argument(
        "--data-dir", help="journal, lock y socket (por defecto ~/.local/state/xbt)"
    )
    parser.add_argument("--live", action="store_true", help="daemon: enviar órdenes reales")
    parser.add_argument("--socket", help="socket de control (por defecto, en el de datos)")
    parser.add_argument(
        "--record", action="store_true", help="daemon: graba books y trades en el de datos"
    )
    parser.add_argument("--replay", metavar="FICHERO", help="daemon y report: usa una grabación")
    args = parser.parse_args()
    paths = resolve_paths(args.config, args.env, args.data_dir)
    if args.command == "report":
        report(args, paths.config, paths.env)
        return
    socket = Path(args.socket).expanduser().absolute() if args.socket else paths.data / SOCKET_NAME
    if args.command != "daemon":
        if args.live:
            raise SystemExit(
                "El cliente no lleva --live: el modo es el del daemon (xbt daemon --live)."
            )
        client(paths.data, socket)
        return
    if problem := replay_problem(args):
        raise SystemExit(problem)
    if not paths.config.exists():
        raise SystemExit(
            f"No existe {paths.config}: cópialo de config.example.toml y revísalo "
            "(o indica otro con --config)."
        )
    venues: dict[VenueId, MarketData] | None = None
    try:
        settings = load_settings(paths.config, paths.env, args.live, replay=bool(args.replay))
        if args.replay:
            recording = Path(args.replay).expanduser().absolute()
            venues = replay_venues(recording, scan_recording(recording))
    except ConfigError as exc:
        raise SystemExit(f"Configuración inválida: {exc}") from None
    except RecordingError as exc:
        raise SystemExit(f"Grabación inválida: {exc}") from None
    if problem := socket_problem(socket):
        raise SystemExit(f"No se puede abrir el socket: {problem}.")
    lock = InstanceLock(paths.data)
    try:
        lock.acquire()
    except AlreadyRunningError as exc:
        raise SystemExit(str(exc)) from None
    diag = setup_diagnostics(paths.data, settings.redactor, settings.config.log.level)
    logging.getLogger(__name__).info("arranque en %s, datos en %s", settings.mode, paths.data)
    try:
        asyncio.run(serve(settings, paths.data, socket, record=args.record, venues=venues))
    finally:
        diag.close()
        lock.release()


if __name__ == "__main__":
    main()
