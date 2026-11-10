"""Entry point: `xbt daemon [--live] [--record] [--replay FILE] [--config PATH]
[--env PATH] [--data-dir PATH] [--socket PATH]`, `xbt report --replay FILE
[--config PATH]` and `xbt [attach] [--data-dir PATH] [--socket PATH]`.

`daemon` is the server: the only process that talks to the venues, holds the keys,
the lock and the journal, with no screen and in the foreground (RF-30). With no
subcommand (or `attach`), the TUI: that daemon's client over its socket, showing
and operating everything through it without reading config or keys (RF-31, D-66).
With no flags, config and `.env` live in `~/.config/xbt` and journal and socket in
`~/.local/state/xbt` (XDG, RNF-20). One daemon per data directory (RNF-17).
`--record` records the market, `--replay` swaps the venues for a recording and
`report` gives the bot's result on it (RF-33 to RF-35, D-80)."""

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
DESCRIPTION = "xbt-terminal: daemon, pantalla e informe sobre una grabación."


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
    """The venues coming out of a recording, with a single time origin."""
    timeline = Timeline(info, clock, venues=len(info.specs))
    return {
        venue_id: ReplayVenue(venue_id, recording, info, timeline, clock) for venue_id in info.specs
    }


def trading_venues(
    settings: Settings,
    venues: dict[VenueId, MarketData],
    hub: MarketHub,
    clock: Callable[[], int] = now_ms,
) -> dict[VenueId, Venue]:
    """Venues to trade on: those with keys. Dry-run: wrapped in `DryRunVenue`, with real
    balances and simulated orders (D-16) and the hub's MarketSpec (D-18). Live: the
    real ones. Replay: `SimVenue` over the recording (RF-34)."""
    if settings.replay:
        cfg = settings.config.replay
        assert cfg is not None  # load_settings does not get here without it
        return {
            venue_id: SimVenue(venue_data, cfg, clock=clock)
            for venue_id, venue_data in venues.items()
            if isinstance(venue_data, ReplayVenue)
        }
    tradable = {
        venue_id: venue_data
        for venue_id, venue_data in venues.items()
        if settings.can_trade(venue_id)
    }
    if settings.mode == "dry-run":
        return {
            venue_id: DryRunVenue(
                venue_data, spec=lambda venue_id=venue_id: hub.state[venue_id].spec, clock=clock
            )
            for venue_id, venue_data in tradable.items()
        }
    out: dict[VenueId, Venue] = {}
    for venue_id, venue in tradable.items():
        if not isinstance(venue, Venue):
            raise TypeError(f"{venue_id}: el adaptador no tiene parte privada")
        out[venue_id] = venue
    return out


def deposit_service(settings: Settings, venues: dict[VenueId, MarketData]) -> DepositService | None:
    """Deposit addresses (RF-28) with the real adapters of venues with keys, in any
    mode: they are only read. Without keys, None."""
    readers = {
        venue_id: venue_data
        for venue_id, venue_data in venues.items()
        if venue_id in settings.credentials and isinstance(venue_data, Depositor)
    }
    return DepositService(readers) if readers else None


def rebalance_service(
    settings: Settings, venues: dict[VenueId, MarketData]
) -> RebalanceService | None:
    """Withdrawal fees for the rebalance plan (RF-29), like deposits: real adapters of
    venues with keys. Without keys, None."""
    readers = {
        venue_id: venue_data
        for venue_id, venue_data in venues.items()
        if venue_id in settings.credentials and isinstance(venue_data, WithdrawalFees)
    }
    return RebalanceService(readers) if readers else None


def results_service(
    directory: Path, settings: Settings, venues: Mapping[VenueId, MarketData]
) -> ResultsService:
    """Results of the journal in `directory` (RF-25), with the fee token price of each
    venue that has one, to value its fees (D-65)."""
    pricers = {}
    for venue_id, venue_config in settings.config.venues.items():
        venue = venues.get(venue_id)
        if venue_config.fee_token is not None and isinstance(venue, TokenPricer):
            pricers[venue_config.fee_token] = lambda venue=venue, token=venue_config.fee_token: (
                venue.token_price(token)
            )
    return ResultsService(directory, pricers=pricers)


def fee_tokens(venues: Mapping[VenueId, VenueConfig]) -> dict[VenueId, FeeToken]:
    """Venues paying fees in a token, for the bot (RF-24, D-63)."""
    return {
        venue_id: FeeToken(
            venue_config.fee_token, venue_config.fee_token_min, venue_config.fee_discount_pct
        )
        for venue_id, venue_config in venues.items()
        if venue_config.fee_token is not None and venue_config.fee_token_min is not None
    }


def build_engine(
    settings: Settings,
    logs: Path = Path("logs"),
    venues: dict[VenueId, MarketData] | None = None,
    clock: Callable[[], int] = now_ms,
) -> Engine:
    """Orders and account share venues and journal, and orders validate with what the
    account already has (D-18). In dry-run the account reads the `DryRunVenue`s (real
    balances, simulated orders); read-only has nothing. Used by the terminal and the
    daemon (D-61). `clock`: the whole engine's; the report passes virtual time (D-80)."""
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
        # one for all: the bar counts the session's
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
        live = settings.mode == "live"  # clock skew only blocks in live (D-30)
        orders = OrderService(
            hub, trading, journal, cfg.risk, account=account, faults=faults, live=live,
            clock=clock,
        )  # fmt: skip
        if cfg.bot is not None:  # phase 2: the bot trades with the same pieces (D-36)
            bot = BotService(
                hub, orders, account, journal, cfg.bot, live=live, faults=faults,
                target=cfg.inventory, fee_tokens=fee_tokens(cfg.venues), clock=clock,
            )  # fmt: skip
    return Engine(
        hub, mode=settings.mode, orders=orders, account=account, bot=bot,
        max_clock_skew_s=cfg.risk.max_clock_skew_s, journal=journal,
        value_every_s=cfg.polling.value_every_s, clock=clock,
        # what the client asks for over the socket (D-66)
        results=results_service(logs, settings, venues),
        deposits=deposit_service(settings, venues),
        rebalance=rebalance_service(settings, venues),
    )  # fmt: skip


def client_view(settings: Settings) -> ClientView:
    """What the client screen needs from the config; travels in `hello`."""
    cfg = settings.config
    return ClientView(
        book_levels=cfg.ui.book_levels,
        timeframe=cfg.ui.default_timeframe,
        fee_discounts={
            venue_id: venue_config.fee_discount_pct for venue_id, venue_config in cfg.venues.items()
        },
        inventory=cfg.inventory,
        bot_limits=cfg.bot.limits if cfg.bot is not None else None,
    )


def build_client_app(socket: Path, hello: Hello) -> XbtApp:
    """The client: everything from the daemon over the socket (RF-31, D-66). No config,
    keys, venues, journal or lock."""
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
        clipboard=system_copy,  # OSC 52 does not reach GNOME Terminal (D-52)
    )


DEFAULT_VIEW = ClientView(15, "1m", {}, None, None)  # a daemon that does not send its config


def client(data: Path, socket: Path) -> None:
    try:
        hello = asyncio.run(probe(socket))
    except DaemonGoneError as error:
        raise SystemExit(f"No se puede conectar: {error}.") from None
    private_dir(data)
    # the client has no keys to redact
    diag = setup_diagnostics(data, Redactor([]), "INFO", name=CLIENT_LOG)
    logging.getLogger(__name__).info("client of %s (%s)", socket, hello.mode)
    try:
        build_client_app(socket, hello).run()
    finally:
        diag.close()
    print("Cliente desconectado: el daemon sigue en marcha.", file=sys.stderr)


def replay_report(settings: Settings, recording: Path) -> ReplayReport:
    """The RF-35 report: the whole engine on the recording, in virtual time and with a
    journal thrown away at the end."""
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
            timeline = next(
                feed.timeline for feed in feeds.values() if isinstance(feed, ReplayVenue)
            )
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
    """The daemon: SIGTERM and SIGINT stop it in order; one stderr line when ready, for
    whoever supervises it (D-61)."""
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
    """What cannot be combined with `--replay` (RF-34), or None."""
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
    """`xbt report`: the RF-35 report on stdout."""
    if args.replay is None:
        raise SystemExit("xbt report necesita --replay FICHERO.")
    try:
        settings = load_settings(config, env, False, replay=True)
        print(render(replay_report(settings, Path(args.replay).expanduser())))
    except (ConfigError, RecordingError) as error:
        raise SystemExit(f"No se puede hacer el informe: {error}") from None


def main() -> None:
    parser = argparse.ArgumentParser(prog="xbt", description=DESCRIPTION)
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
    except ConfigError as error:
        raise SystemExit(f"Configuración inválida: {error}") from None
    except RecordingError as error:
        raise SystemExit(f"Grabación inválida: {error}") from None
    if problem := socket_problem(socket):
        raise SystemExit(f"No se puede abrir el socket: {problem}.")
    lock = InstanceLock(paths.data)
    try:
        lock.acquire()
    except AlreadyRunningError as error:
        raise SystemExit(str(error)) from None
    diag = setup_diagnostics(paths.data, settings.redactor, settings.config.log.level)
    logging.getLogger(__name__).info("starting in %s, data in %s", settings.mode, paths.data)
    try:
        asyncio.run(serve(settings, paths.data, socket, record=args.record, venues=venues))
    finally:
        diag.close()
        lock.release()


if __name__ == "__main__":
    main()
