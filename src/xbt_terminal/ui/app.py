"""App Textual: layout 2x3, paneles y línea de comandos.

    ┌ velas nk ─────────┬ velas nx ─────────┐  1fr
    ├ book nk ──────────┼ book nx ──────────┤  N niveles + 4
    ├ barra inferior (dos columnas) ────────┤
    └ línea de comandos ────────────────────┘

La UI es el cliente del daemon (RF-31, D-66): habla con las caras de
`services/remote.py` (mercado, cuenta, órdenes, bot, resultados, depósitos y
reequilibrio) y no importa ningún servicio que hable con los venues. Los tests
le pasan los servicios de verdad, que tienen la misma cara, y un `Runner` que
corre sus bucles. Los avisos del mercado marcan paneles como sucios
y se redibujan con throttle: books cada 100 ms (RNF-06 pide < 500 ms) y velas
cada segundo. Por debajo de 160×45 se muestra un aviso en lugar del layout.

Una orden con envío incierto (RNF-12) se avisa en rojo y la barra de estado las
cuenta; la reconciliación la hace el daemon y sus avisos llegan como `Notice`.

Las pantallas modales (confirmar, ayuda, órdenes, resultados, depósitos y
reequilibrio) están en `ui/screens/`, una por módulo (T-83).

Spec: RF-02..RF-08, RF-25, RF-28, RF-29, RNF-02, RNF-06, RNF-12 · design.md §9,
§6.10, §6.11, §6.12
Tareas: T-13..T-19, T-25, T-40, T-42, T-45, T-65, T-83
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.geometry import Size
from textual.widgets import Input, Static

from ..core.bot import BotState
from ..core.commands import (
    BotCommand,
    CancelAll,
    Command,
    CommandError,
    DoubleOrder,
    ListOrders,
    PlaceOrder,
    SetTimeframe,
    ShowDeposits,
    ShowHelp,
    ShowRebalance,
    ShowResults,
    parse,
)
from ..core.cross import Quote
from ..core.inventory import inventory
from ..core.models import BASE_LABEL, ConnState, Order, Timeframe, VenueId
from ..core.notices import Fault, Notice
from ..core.proposals import (
    CancelPlan,
    CancelRefused,
    DoubleRejected,
    DoubleSent,
    Failed,
    Pending,
    PendingDouble,
    Rejected,
    Sent,
    Uncertain,
)
from ..core.rebalance import Holding, holdings
from ..core.results import Range
from ..services.remote import (
    DaemonGoneError,
    DaemonRefusedError,
    RemoteAccount,
    RemoteBot,
    RemoteDeposits,
    RemoteEngine,
    RemoteMarket,
    RemoteOrders,
    RemoteRebalance,
    RemoteResults,
)
from ..services.wire import ClientView
from .clipboard import Clipboard
from .panels import (
    VENUE_NAME,
    AccountView,
    BalancesPanel,
    BookPanel,
    ChartPanel,
    CommandLine,
    CrossPanel,
    LegsPanel,
)
from .screens.confirm import ConfirmScreen
from .screens.deposit import DepositScreen
from .screens.help import HelpScreen
from .screens.orders import CancelCommand, OrdersScreen
from .screens.rebalance import RebalanceScreen
from .screens.results import ResultsScreen
from .status import StatusBar, VenueLink

MIN_SIZE = (160, 45)  # RNF-02
VENUES: tuple[VenueId, ...] = ("nk", "nx")  # izquierda, derecha
BOOK_EVERY_S = 0.1
CHART_EVERY_S = 1.0
STATUS_EVERY_S = 1.0  # antigüedad de los estados; los cambios se pintan al llegar
# conectada al daemon todo pasa por el socket (RF-31, D-66)
ATTACHED_HINT = (
    "cliente del daemon: buy · sell · dbl · cancel · bot on/off · results · ? ayuda"
    " · q desconecta (el daemon sigue)"
)


class Runner(Protocol):
    """Lo que la app necesita de quien corre los servicios: con daemon, un
    `Engine` sin órdenes sobre el mercado remoto; en los tests, el de verdad."""

    @property
    def fault_count(self) -> int: ...

    def report_fault(self, where: str, exc: BaseException) -> Fault: ...
    def subscribe_faults(self, listener: Callable[[Fault], None]) -> None: ...
    def subscribe(self, listener: Callable[[Notice], None]) -> None: ...
    def services(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]: ...
    def watchers(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]: ...
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ClientServices:
    """Las caras del daemon que usa la pantalla (RF-31); en los tests, los servicios
    de verdad, que tienen la misma cara. Sin ninguna, la pantalla solo mira."""

    orders: RemoteOrders | None = None
    account: RemoteAccount | None = None
    bot: RemoteBot | None = None
    results: RemoteResults | None = None
    deposits: RemoteDeposits | None = None  # solo venues con keys (RF-28)
    rebalance: RemoteRebalance | None = None  # comisiones de retiro (RF-29)
    remote: RemoteEngine | None = None  # conectada al daemon: mira y arma o para el bot


READ_ONLY = ClientServices()  # sin caras: la pantalla solo mira


class TooSmall(Static):
    DEFAULT_CSS = """
    TooSmall { width: 100%; height: 100%; content-align: center middle; }
    """

    def __init__(self) -> None:
        super().__init__(id="too-small")
        self.text = ""

    def show_size(self, width: int, height: int) -> None:
        self.text = (
            f"La terminal mide {width}×{height}; hacen falta al menos "
            f"{MIN_SIZE[0]}×{MIN_SIZE[1]}. Agrándala o reduce la fuente."
        )
        self.update(self.text)



class XbtApp(App[None]):
    TITLE = f"Terminal {BASE_LABEL}"
    # sin foco inicial en la línea de comandos: si no, 1-5/q se escribirían en ella
    AUTO_FOCUS = None
    CSS = """
    #layout {
        layout: grid;
        grid-size: 2 3;
        grid-gutter: 0;
    }
    #bottom { column-span: 2; height: auto; }
    #account-row { height: auto; }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("1", "timeframe('1m')", "1m"),
        Binding("2", "timeframe('5m')", "5m"),
        Binding("3", "timeframe('1h')", "1h"),
        Binding("4", "timeframe('4h')", "4h"),
        Binding("5", "timeframe('1d')", "1d"),
        Binding("q", "quit", "salir"),
        Binding("b", "command('buy ')", "comprar"),
        Binding("s", "command('sell ')", "vender"),
        Binding("d", "command('dbl ')", "orden doble"),
        Binding("o", "orders", "órdenes"),
        Binding("r", "results", "resultados"),
        Binding("w", "deposits", "depósitos"),
        Binding("e", "rebalance", "reequilibrio"),
        Binding("x", "cancel_all", "cancelar todo"),
        Binding("p", "bot_off", "parar bot"),
        # parada del bot que llega aunque la línea de comandos tenga el foco (D-39)
        Binding("ctrl+b", "bot_off", "parar bot", priority=True),
        Binding("question_mark", "help", "ayuda"),
        Binding("escape", "release", show=False),
    ]

    def __init__(
        self,
        hub: RemoteMarket,
        *,
        view: ClientView,
        mode: str,
        engine: Runner,
        services: ClientServices = READ_ONLY,
        can_trade: Mapping[VenueId, bool] | None = None,
        run_hub: bool = True,
        clock: Callable[[], int] | None = None,
        startup_warnings: Sequence[str] = (),
        clipboard: Clipboard | None = None,
    ) -> None:
        super().__init__()
        self.hub = hub
        # la config que manda el daemon en `hello` (D-66)
        self.book_levels = view.book_levels
        self.timeframe: Timeframe = view.timeframe
        self.inventory_target = view.inventory  # ratio de inventario (RF-27)
        self.bot_limits = view.bot_limits  # para las dobles que pagan el reequilibrio
        # descuento de fee por venue, solo para el panel cruzado (RF-24, D-38)
        self._fee_discounts = dict(view.fee_discounts)
        # las caras del daemon (RF-31)
        self.remote = services.remote
        self.orders = services.orders
        self.account = services.account
        self.bot = services.bot
        self.results = services.results
        self.deposits = services.deposits
        self.rebalance = services.rebalance
        self.mode = mode
        self._run_hub = run_hub
        self.can_trade = dict(can_trade or {})
        # copia con wl-copy/xclip/xsel además de OSC 52; None en tests (D-52)
        self.system_clipboard = clipboard
        self.last_message = ""
        self.last_error = False
        self._clock = clock or hub.clock  # el del hub: ui no importa adapters (D-54)
        self.startup_warnings = tuple(startup_warnings)
        self._dirty_books: set[VenueId] = set()
        self._dirty_charts: set[VenueId] = set()
        self._shown_double: str | None = None
        # avisos, fallos y bucles que no dependen de la pantalla (D-61, D-66)
        self.engine = engine

    def compose(self) -> ComposeResult:
        yield StatusBar()
        yield TooSmall()
        with Container(id="layout"):
            for venue in VENUES:
                yield ChartPanel(venue)
            for venue in VENUES:
                yield BookPanel(venue, self.book_levels)
            with Vertical(id="bottom"):
                yield CrossPanel()
                with Horizontal(id="account-row"):  # piernas a la derecha (D-67)
                    yield BalancesPanel(
                        visible=self.account is not None, target=self.inventory_target
                    )
                    yield LegsPanel()
                yield CommandLine(
                    enabled=self.orders is not None or self.remote is not None,
                    hint=ATTACHED_HINT if self.remote is not None else None,
                )

    def on_mount(self) -> None:
        self.hub.subscribe(self._on_hub)
        # velas | book (N niveles + cabecera, resumen y bordes) | barra y comandos
        self.query_one("#layout").styles.grid_rows = f"1fr {self.book_levels + 4} auto"
        self._apply_size(self.size)
        self.set_interval(BOOK_EVERY_S, self._flush_books)
        self.set_interval(CHART_EVERY_S, self._flush_charts)
        self.set_interval(1.0, self.hub.roll)
        self.set_interval(STATUS_EVERY_S, self._draw_status)
        self._draw_status()
        for warning in self.startup_warnings:
            self.say(f"⚠ {warning}", error=True)
        if self.bot is not None:
            self.bot.subscribe(self._on_bot)
        self.engine.subscribe_faults(self._on_fault)
        if self.account is not None:
            self.account.subscribe(self._on_account)
        if self.orders is not None:
            self.orders.subscribe_doubles(self._on_double)
        self.engine.subscribe(self._on_notice)
        if self.remote is not None:
            self.remote.subscribe(self._on_notice)
            self.remote.subscribe_link(lambda _up: self.call_later(self._draw_status))
            self.run_worker(self.remote.run(), name="daemon", exit_on_error=False)
        # los servicios aíslan sus fallos (RNF-14): ningún worker cierra la app
        tasks = self.engine.watchers() + (self.engine.services() if self._run_hub else [])
        for name, work in tasks:
            self.run_worker(work(), name=name, exit_on_error=False)

    async def on_unmount(self) -> None:
        await self.engine.aclose()  # envíos y cancelaciones en vuelo, al journal (RNF-15)
        if self.remote is not None:
            await self.remote.aclose()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_size(event.size)  # self.size aún no está al día

    def _apply_size(self, size: Size) -> None:
        width, height = size
        big = width >= MIN_SIZE[0] and height >= MIN_SIZE[1]
        self.query_one("#layout").display = big
        warning = self.query_one(TooSmall)
        warning.display = not big
        warning.show_size(width, height)

    def _on_hub(self, venue: VenueId, what: str) -> None:
        if what == "conn":
            self.call_later(self._draw_status)
        if what in ("book", "market"):
            self._dirty_books.add(venue)
        if what in ("candles", "market"):
            self._dirty_charts.add(venue)

    def _on_account(self, venue: VenueId, what: str) -> None:
        if what == "conn":
            self.call_later(self._draw_status)
        if what in ("balances", "orders", "fill"):
            self.call_later(self._draw_balances)
        if what == "orders":
            for screen in self.screen_stack:
                if isinstance(screen, OrdersScreen):
                    self.call_later(screen.refresh_rows)
        if what == "fill":
            for screen in self.screen_stack:
                if isinstance(screen, ResultsScreen):
                    self.call_later(screen.refresh_report)

    def _draw_balances(self) -> None:
        if self.account is None:
            return
        views: dict[VenueId, AccountView | None] = {}
        for vid in VENUES:
            st = self.account.state.get(vid)
            views[vid] = (
                AccountView(st.balances, st.balances_at, st.error) if st is not None else None
            )
        inv = inventory(
            {vid: st.balances for vid, st in self.account.state.items()},
            {vid: self.hub.state[vid].book.mid for vid in self.account.state},
        )
        try:
            self.query_one(BalancesPanel).show(views, inv, self._clock())
        except NoMatches:  # puede llegar un aviso mientras se desmonta
            pass

    def _open_orders(self) -> list[Order]:
        if self.account is None:
            return []
        out: list[Order] = []
        for vid in VENUES:
            st = self.account.state.get(vid)
            if st is not None:
                out += sorted(st.orders.values(), key=lambda o: (o.ts, o.order_id))
        return out

    def _flush_books(self) -> None:
        if not self._dirty_books:
            return
        try:
            self._draw_books()
        except NoMatches:  # el temporizador puede saltar mientras se desmonta
            pass

    def _draw_books(self) -> None:
        for venue in self._dirty_books:
            st = self.hub.state[venue]
            self.query_one(f"#book-{venue}", BookPanel).show(st.book, st.spec)
        self._dirty_books.clear()
        notes = [
            f"{VENUE_NAME[v]} con fee -{pct:f} %" for v, pct in self._fee_discounts.items() if pct
        ]
        self.query_one(CrossPanel).show(self._quote("nk"), self._quote("nx"), ", ".join(notes))

    def _private_state(self, venue: VenueId) -> ConnState | None:
        if self.account is None or venue not in self.account.state:
            return None
        return self.account.state[venue].conn

    def _draw_status(self) -> None:
        remote = self.remote
        can_trade = remote.can_trade if remote is not None else self.can_trade
        links = {
            venue: VenueLink(
                market=self.hub.state[venue].conn,
                seen_at=self.hub.state[venue].conn_at,
                can_trade=can_trade.get(venue, False),
                private=self._private_state(venue),
            )
            for venue in VENUES
        }
        uncertain = len(self.orders.uncertain) if self.orders is not None else 0
        faults = self.engine.fault_count
        if remote is not None:  # lo del daemon (RF-31)
            uncertain, faults = len(remote.uncertain), faults + remote.faults
        bot = self.bot.state.status if self.bot is not None else None
        try:
            self.query_one(StatusBar).show(
                links, self.mode, self._clock(), uncertain=uncertain, faults=faults, bot=bot,
                daemon=remote.connected if remote is not None else None,
            )  # fmt: skip
        except NoMatches:  # el temporizador puede saltar mientras se desmonta
            pass
        self._draw_balances()  # la antigüedad de los saldos avanza con el reloj
        if remote is not None:
            self._follow_remote_legs()

    def _quote(self, venue: VenueId) -> Quote | None:
        """Mejores precios y fee taker del venue; None sin spec (sin fee no hay neto)."""
        st = self.hub.state[venue]
        if st.spec is None:
            return None
        discount = self._fee_discounts.get(venue, Decimal(0))
        taker = st.spec.taker_fee * (1 - discount / 100)
        return Quote(venue, st.book.best_bid, st.book.best_ask, taker_fee=taker)

    def _flush_charts(self) -> None:
        try:
            for venue in self._dirty_charts:
                self._draw_chart(venue)
        except NoMatches:  # el temporizador puede saltar mientras se desmonta
            pass
        self._dirty_charts.clear()

    def _draw_chart(self, venue: VenueId) -> None:
        candles = self.hub.state[venue].candles[self.timeframe].candles
        self.query_one(f"#chart-{venue}", ChartPanel).show(candles, self.timeframe)

    def action_timeframe(self, timeframe: Timeframe) -> None:
        self.timeframe = timeframe
        for venue in VENUES:
            self._draw_chart(venue)

    # -- salir (RNF-15, D-29) -------------------------------------------------------

    async def action_quit(self) -> None:
        """En live, con órdenes abiertas, se pregunta: siguen en el exchange al salir.
        Conectada al daemon, solo se desconecta: no hay nada que preguntar."""
        if self.remote is not None:
            self.say("desconectando: el daemon y el bot siguen igual")
            self.exit()
            return
        n = len(self._open_orders()) if self.mode == "live" else 0
        if not n:
            self._quit()
            return
        if isinstance(self.screen, ConfirmScreen):
            return  # ya hay un diálogo abierto
        what = "1 orden abierta" if n == 1 else f"{n} órdenes abiertas"
        summary = f"hay {what} en el exchange: siguen ahí al salir (no se cancelan)"
        self.push_screen(ConfirmScreen(summary, self.mode, action="quit"), self._quit_confirmed)

    def _quit_confirmed(self, yes: bool | None) -> None:
        if yes:
            self._quit()

    def _quit(self) -> None:
        if self.orders is not None and self.orders.in_flight:
            self.say(f"saliendo: se espera a {self.orders.in_flight} envío(s) en vuelo")
        self.exit()

    # -- fallos (RNF-14, D-28) -----------------------------------------------------

    def _on_fault(self, fault: Fault) -> None:
        self.call_later(self._show_fault, fault)

    def _on_notice(self, notice: Notice) -> None:
        self.call_later(self._show_notice, notice)

    def _show_notice(self, notice: Notice) -> None:
        self.say(notice.text, error=notice.error)
        self._draw_status()

    def _show_fault(self, fault: Fault) -> None:
        self.say(fault.message, error=True)
        self._draw_status()

    async def _guarded(self, work: Awaitable[None], where: str = "ui") -> None:
        """Un bug en una acción se reporta y la terminal sigue."""
        try:
            await work
        except (DaemonGoneError, DaemonRefusedError) as exc:
            self.say(str(exc), error=True)
        except Exception as exc:
            self.engine.report_fault(where, exc)

    # -- comandos ---------------------------------------------------------------

    def say(self, message: str, *, error: bool = False) -> None:
        self.last_message, self.last_error = message, error
        # sin markup: los errores del venue pueden traer corchetes ("[1002]")
        self.notify(message, severity="error" if error else "information", timeout=8, markup=False)

    def action_command(self, prefix: str) -> None:
        cmd = self.query_one(CommandLine)
        if cmd.disabled:
            return
        cmd.value = prefix
        cmd.place_cursor(len(prefix))

    def action_release(self) -> None:
        self.set_focus(None)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self._guarded(self._submitted(event))

    async def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        try:
            command = parse(text)
        except CommandError as exc:
            self.say(str(exc), error=True)
            return
        try:
            await self._dispatch(command)
        # conectada: el daemon no está o no quiso
        except (DaemonGoneError, DaemonRefusedError) as exc:
            self.say(str(exc), error=True)

    async def _dispatch(self, command: Command) -> None:
        if isinstance(command, SetTimeframe):
            self.action_timeframe(command.timeframe)
            self.say(f"velas en {command.timeframe}")
        elif isinstance(command, PlaceOrder):
            await self._place(command)
        elif isinstance(command, DoubleOrder):
            await self._double(command)
        elif isinstance(command, ListOrders):
            self._show_orders(command.venue)
        elif isinstance(command, ShowHelp):
            self.action_help()
        elif isinstance(command, ShowResults):
            self._show_results(command.range)
        elif isinstance(command, ShowDeposits):
            self.action_deposits()
        elif isinstance(command, ShowRebalance):
            self.action_rebalance()
        elif isinstance(command, BotCommand):
            self._bot_command(command)
        else:
            await self._cancel(command)

    # -- bot (fase 2) -----------------------------------------------------------

    def _bot_command(self, command: BotCommand) -> None:
        if self.bot is None:
            why = (
                "no hay bot en solo lectura"
                if self.mode == "read-only"
                else "no hay bot: falta la sección [bot] en config.toml"
            )
            self.say(why, error=True)
            return
        if command.action == "off":
            self.action_bot_off()
        elif command.action == "status":
            self.say(self.bot.status_text(), error=self.bot.state.status == "halted")
        elif refusal := self.bot.refusal():
            self.say(refusal, error=True)
        else:
            screen = ConfirmScreen(self.bot.summary, self.mode, action="bot")
            self.push_screen(screen, self._bot_confirmed)

    def _bot_confirmed(self, yes: bool | None) -> None:
        if not yes or self.bot is None:
            self.say("bot sin cambios: no se armó")
            return
        if refusal := self.bot.arm():
            self.say(refusal, error=True)
            return
        self.say("bot armado: dispara cuando el cruce neto supere el umbral (ctrl+b lo para)")

    def action_bot_off(self) -> None:
        if self.bot is None or self.bot.state.status == "off":
            return
        self.bot.disarm()
        self.say("bot parado a mano: no dispara hasta otro bot on")

    def _on_bot(self, state: BotState) -> None:
        self._draw_status()
        if state.status == "halted":
            self.say(f"bot PARADO: {state.reason}. Revisa y rearma con bot on.", error=True)

    async def _place(self, command: PlaceOrder) -> None:
        if self.orders is None:
            self.say("no se pueden enviar órdenes en este modo", error=True)
            return
        prepared = await self.orders.prepare(command)
        if isinstance(prepared, Rejected):
            self.say(prepared.message, error=True)
            return
        self.push_screen(ConfirmScreen(prepared.summary, self.mode), self._confirmed(prepared))

    def _confirmed(self, pending: Pending):
        async def callback(yes: bool | None) -> None:
            await self._guarded(send(yes))

        async def send(yes: bool | None) -> None:
            if not yes:
                self.say("orden cancelada, no se envió nada")
                return
            assert self.orders is not None
            result = await self.orders.confirm(pending)
            if isinstance(result, Sent):
                o = result.order
                self.say(f"enviada: {o.order_id} {o.side} {o.qty} @ {o.price} ({o.status})")
            elif isinstance(result, Rejected):
                self.say(result.message, error=True)
            elif isinstance(result, Failed):
                prefix = "" if result.local else "error del venue: "
                self.say(f"{prefix}{result.error}", error=True)
            elif isinstance(result, Uncertain):
                self._say_uncertain(result)

        return callback

    # -- orden doble ------------------------------------------------------------

    async def _double(self, command: DoubleOrder) -> None:
        if self.orders is None:
            self.say("no se pueden enviar órdenes en este modo", error=True)
            return
        prepared = await self.orders.prepare_double(command)
        if isinstance(prepared, DoubleRejected):
            self.say(prepared.message, error=True)
            return
        self.push_screen(
            ConfirmScreen(prepared.summary, self.mode), self._double_confirmed(prepared)
        )

    def _double_confirmed(self, pending: PendingDouble):
        async def callback(yes: bool | None) -> None:
            await self._guarded(send(yes))

        async def send(yes: bool | None) -> None:
            if not yes:
                self.say("orden doble cancelada, no se envió nada")
                return
            assert self.orders is not None
            result = await self.orders.confirm_double(pending)
            if isinstance(result, DoubleRejected):
                self.say(result.message, error=True)
                return
            assert isinstance(result, DoubleSent)
            st = result.state
            self._shown_double = st.id
            self._draw_legs()
            legs = " · ".join(f"{leg.label}: {leg.status}" for leg in st.legs)
            failed = any(leg.status in ("rejected", "unknown") for leg in st.legs)
            if any(leg.status == "unknown" for leg in st.legs):
                self._draw_status()
            self.say(f"orden doble {st.id} enviada: {legs}", error=failed)

        return callback

    def _on_double(self, double_id: str) -> None:
        if double_id == self._shown_double:
            self.call_later(self._draw_legs)

    def _follow_remote_legs(self) -> None:
        """Conectada al daemon: la última doble que el daemon conoce."""
        assert self.remote is not None
        doubles = self.remote.doubles
        if doubles:
            self._shown_double = max(doubles.values(), key=lambda d: d.created).id
            self._draw_legs()

    def _draw_legs(self) -> None:
        source = self.remote if self.remote is not None else self.orders
        if source is None or self._shown_double is None:
            return
        state = source.doubles.get(self._shown_double)
        if state is None:  # con daemon, la foto que la trae aún no ha llegado: se pinta al llegar
            return
        specs = {vid: self.hub.state[vid].spec for vid in VENUES}
        try:
            self.query_one(LegsPanel).show(state, source.hanging.get(state.id), specs)
        except NoMatches:  # puede llegar un aviso mientras se desmonta
            pass

    # -- envíos inciertos (RNF-12) ------------------------------------------------

    def _say_uncertain(self, result: Uncertain) -> None:
        r = result.req
        self.say(
            f"⚠ orden INCIERTA {r.client_id} en {VENUE_NAME[r.venue]}: {result.error}. "
            "Puede existir: se está comprobando en el venue",
            error=True,
        )
        self._draw_status()

    # -- órdenes abiertas y cancelar ---------------------------------------------

    def action_orders(self) -> None:
        self._show_orders(None)

    def _show_orders(self, venue: VenueId | None) -> None:
        if self.account is None:
            self.say("solo lectura: sin keys no hay órdenes propias", error=True)
            return
        self.push_screen(OrdersScreen(self._open_orders, venue, self._request_cancel))

    def _request_cancel(self, command: CancelCommand) -> None:
        self.run_worker(self._guarded(self._cancel(command)), group="cancel", exit_on_error=False)

    async def action_cancel_all(self) -> None:
        await self._guarded(self._cancel(CancelAll()))

    async def _cancel(self, command: CancelCommand) -> None:
        if self.orders is None:
            self.say("no se puede cancelar en este modo", error=True)
            return
        plan = await self.orders.plan_cancel(command)
        if isinstance(plan, CancelRefused):
            self.say(plan.message, error=True)
            return
        self.push_screen(
            ConfirmScreen(plan.summary, self.mode, action="cancel"), self._cancel_confirmed(plan)
        )

    def _cancel_confirmed(self, plan: CancelPlan):
        async def callback(yes: bool | None) -> None:
            await self._guarded(send(yes))

        async def send(yes: bool | None) -> None:
            if not yes:
                self.say("no se canceló nada")
                return
            assert self.orders is not None
            result = await self.orders.execute_cancel(plan)
            self.say(result.message, error=not result.ok)

        return callback

    # -- ayuda y clic en el book -------------------------------------------------

    def action_results(self) -> None:
        self._show_results("dia")

    def _show_results(self, rng: Range) -> None:
        if self.results is None:
            self.say("resultados: no hay directorio del journal", error=True)
            return
        self.push_screen(ResultsScreen(self.results, rng, lambda m: self.say(m, error=True)))

    def action_deposits(self) -> None:
        if self.deposits is None or not self.deposits.venues:
            self.say("depósitos: no hay keys de ningún venue", error=True)
            return
        self.push_screen(DepositScreen(self.deposits, self.copy_text))

    def action_rebalance(self) -> None:
        if self.rebalance is None or not self.rebalance.venues:
            self.say("reequilibrio: no hay keys de ningún venue", error=True)
            return
        self.push_screen(
            RebalanceScreen(
                self.rebalance, self.rebalance_holdings, self.inventory_target, self.bot_limits
            )
        )

    def rebalance_holdings(self) -> Mapping[VenueId, Holding] | str:
        """Saldos de los dos venues y su mid, o por qué faltan (RF-29)."""
        if self.account is None:
            return "sin saldos: hace falta abrir la terminal con keys"
        balances = {
            vid: st.balances if (st := self.account.state.get(vid)) is not None else None
            for vid in VENUES
        }
        return holdings(balances, {vid: self.hub.state[vid].book.mid for vid in VENUES})

    async def copy_text(self, text: str) -> str | None:
        """OSC 52 siempre y, si hay, la herramienta del sistema, cuyo nombre
        devuelve (D-52): GNOME Terminal ignora OSC 52 sin avisar."""
        try:
            self.copy_to_clipboard(text)
        except Exception:  # el portapapeles es un extra: OSC 52 no siempre existe
            pass
        return None if self.system_clipboard is None else await self.system_clipboard(text)

    def action_help(self) -> None:
        self.push_screen(HelpScreen(self.mode))

    def on_book_panel_picked(self, event: BookPanel.Picked) -> None:
        """RF-16, D-18: con la línea vacía, la orden entera (precio y cantidad del
        nivel); con texto, solo el precio en el cursor."""
        price = f"{event.price:.{event.price_decimals}f}"
        self.run_worker(self.copy_text(price), group="copy")
        cmd = self.query_one(CommandLine)
        if cmd.disabled:
            self.say(f"precio {price} copiado (solo lectura: sin órdenes)")
            return
        if not cmd.value.strip():
            verb = "buy" if event.side == "ask" else "sell"
            qty = f"{event.qty:.{event.qty_decimals}f}"
            cmd.value = f"{verb} {event.venue} {qty} {price}"
            cmd.place_cursor(len(cmd.value))
            self.say(f"{verb} {qty} @ {price}: Enter para confirmar, o edítala")
            return
        at = cmd.cursor_position
        before, after = cmd.value[:at], cmd.value[at:]
        sep = "" if not before or before.endswith(" ") else " "
        cmd.value = before + sep + price + after
        cmd.place_cursor(len(before + sep + price))
        self.say(f"precio {price} en la orden")
