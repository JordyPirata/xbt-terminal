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

Spec: RF-02..RF-08, RF-25, RF-28, RF-29, RNF-02, RNF-06, RNF-12 · design.md §9,
§6.10, §6.11, §6.12
Tareas: T-13..T-19, T-25, T-40, T-42, T-45, T-65
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.geometry import Size
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static, TabbedContent, TabPane

from ..core.bot import BotLimits, BotState
from ..core.commands import (
    BotCommand,
    CancelAll,
    CancelOrder,
    CancelVenue,
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
from ..core.cross import Quote, sats
from ..core.inventory import InventoryTarget, inventory
from ..core.models import (
    BASE_LABEL,
    ConnState,
    DepositAddress,
    DepositRow,
    Order,
    Timeframe,
    VenueId,
    asset_label,
)
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
from ..core.rebalance import Holding, Plan, Transfer, break_even, holdings, plan
from ..core.results import DoubleResult, Range, Report
from ..core.valuation import ValueSample, last_stretch, summarize
from ..services.remote import (
    DaemonGone,
    DaemonRefused,
    RemoteAccount,
    RemoteBot,
    RemoteDeposits,
    RemoteEngine,
    RemoteMarket,
    RemoteOrders,
    RemoteRebalance,
    RemoteResults,
)
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
    ResultsChart,
    ValueChart,
)
from .qr import qr_lines
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

    faults: list[Any]

    def subscribe(self, listener: Callable[[Notice], None]) -> None: ...
    def services(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]: ...
    def watchers(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]: ...
    async def aclose(self) -> None: ...


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


CancelCommand = CancelOrder | CancelVenue | CancelAll
CONFIRM_LABELS = {
    "order": {"dry-run": "DRY-RUN (simulada)", "live": "LIVE · ORDEN REAL"},
    "cancel": {"dry-run": "DRY-RUN (cancelación simulada)", "live": "LIVE · CANCELACIÓN REAL"},
    "quit": {"live": "LIVE · SALIR CON ÓRDENES ABIERTAS"},
    "bot": {
        "dry-run": "DRY-RUN · ARMAR EL BOT (órdenes simuladas)",
        "live": "LIVE · ARMAR EL BOT: ÓRDENES REALES AUTOMÁTICAS",
    },
}


class ConfirmScreen(ModalScreen[bool]):
    """Confirmación de una orden o una cancelación (design.md §6.2). Por defecto, no."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Static {
        width: 90; height: auto; padding: 1 2; border: thick $warning; background: $panel;
    }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("y", "answer(True)", "confirmar"),
        Binding("n", "answer(False)", "cancelar"),
        Binding("escape", "answer(False)", "cancelar"),
        Binding("enter", "answer(False)", "cancelar"),
    ]

    def __init__(self, summary: str, mode: str, action: str = "order") -> None:
        super().__init__()
        self.summary = summary
        label = CONFIRM_LABELS[action].get(mode, mode)
        self.text = f"{label}\n\n{summary}\n\n[y] confirmar   [n / Esc / Enter] cancelar"

    def compose(self) -> ComposeResult:
        # sin markup: "[y]" se tomaría como etiqueta de Rich y no se vería
        yield Static(self.text, markup=False)

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


MODE_HELP = {
    "read-only": "SOLO LECTURA: sin keys en .env. Se ven books, velas y el panel cruzado; "
    "no hay órdenes ni saldos.",
    "dry-run": "DRY-RUN: lee tus saldos reales y SIMULA las órdenes. Nada sale al exchange. "
    "Es el modo por defecto con keys.",
    "live": "LIVE · ÓRDENES REALES: cada orden confirmada se envía al exchange. "
    "Se arranca con --live.",
}


def help_text(mode: str) -> str:
    return f"""AYUDA · Terminal {BASE_LABEL} ({BASE_LABEL}/BTC en NonKYC y Neoxa)

MODO ACTUAL
  {MODE_HELP.get(mode, mode)}
  Ninguna orden ni cancelación sale sin pulsar [y] en el diálogo; n, Esc o Enter
  cancelan, que es lo que pasa por defecto.

TECLAS (con la línea de comandos sin foco; Esc la suelta)
  b                 empieza una compra en la línea de comandos
  s                 empieza una venta
  d                 empieza una orden doble
  o                 órdenes abiertas (en la lista: c cancela la seleccionada,
                    x cancela todas, Esc cierra)
  x                 cancelar todas las órdenes abiertas (pide confirmación)
  r                 resultados de las órdenes dobles (1 día, 2 7 días, 3 todo)
  ctrl+b  /  p      parar el bot de arbitraje al momento (ctrl+b funciona
                    también con la línea de comandos activa)
  1 2 3 4 5         velas de 1m, 5m, 1h, 4h y 1d (hora UTC)
  ?                 esta ayuda
  q                 salir (en live, con órdenes abiertas, pide confirmación:
                    siguen en el exchange)

CLIC EN EL BOOK
  Con la línea de comandos vacía, un clic en un nivel de VENTA (rojo) escribe
  una compra a ese precio en ese venue, y uno de COMPRA (verde) una venta, con
  la cantidad de ese nivel; un clic en la columna "acumulado" usa la cantidad
  acumulada hasta ahí. Pulsa Enter para ver la confirmación, o edítala antes.
  Si pasa de los topes de riesgo, se rechaza y dice por qué.
  Con algo escrito, el clic solo pega el precio donde está el cursor (útil para
  la orden doble). El precio se intenta copiar también al portapapeles.

COMANDOS (venue: nk = NonKYC, nx = Neoxa)
  buy nk 0.01 0.00725000                      compra límite de 0.01 {BASE_LABEL}
  sell nx 0.01 0.00730000                     venta límite
  buy nx 25% 0.00725000                       compra con el 25 % del BTC de Neoxa
  sell nk 100% 0.00730000                     vende todo el {BASE_LABEL} de NonKYC
    (% del saldo total del venue: BTC al comprar, con la fee descontada; {BASE_LABEL}
    al vender; hacia abajo al lote. Los topes de riesgo se aplican igual.)
  dbl 0.01 nk 0.00725000 nx 0.00730000        orden doble: compra en nk, vende en nx
  orders  /  orders nk                        órdenes abiertas (todas o de un venue)
  cancel <id>                                 cancela una orden (el id sale en orders)
  cancel nk  /  cancel all                    todas las de un venue, o todas
  tf 1m|5m|1h|4h|1d                           timeframe de las velas
  bot on  /  bot off  /  bot status           bot de arbitraje (ver abajo)
  results  /  results 7d  /  results todo     resultados de las dobles (ver abajo)
  deposit                                     direcciones de depósito (tecla w)
  rebalance                                   plan de reequilibrio (tecla e)
  help                                        esta ayuda
  Ojo: cancelar un venue o todo cancela todas las órdenes {BASE_LABEL}/BTC de la
  cuenta, también las que no se enviaron desde la terminal.

CÓMO LEER LA PANTALLA
  Arriba, por venue: ws o respaldo (REST), ● conectado / ◌ reconectando / ✖ caído,
  latencia, y "privado" (● ws o ● rest: el canal de tu cuenta; simulado en dry-run).
  "sin noticias" = el estado no se ha renovado en 15 s.
  Book: compras en verde a la izquierda, ventas en rojo a la derecha; un nivel
  marcado con " A " es liquidez del pool AMM de Neoxa.
  Panel cruzado: mid y spread de cada venue, y las dos rutas comprar-aquí /
  vender-allí, en bruto y neto de fees taker (verde si el neto es positivo).
  Balances: disponible + en órdenes, valor en BTC al mid y % del valor en {BASE_LABEL}
  (verde dentro de la banda de [inventory], amarillo fuera); total en el título.
  Orden doble: estado de cada pierna; "PIERNA COLGADA" en rojo si una se llena y
  la otra no la sigue en 30 s (no se hace nada automático).

BOT DE ARBITRAJE (sección [bot] de config.toml)
  bot on pide confirmación con sus límites y lo arma; en live, solo con
  enabled = true. Cuando comprar en un venue y vender en el otro deja más de la
  neta mínima (con slippage y fee taker), manda una orden doble con límites
  agresivas y cancela lo que no llene a tiempo. Una doble a la vez, con pausa
  entre disparos y topes por hora y por día.
  Con [inventory] en config.toml, no dispara si la doble dejaría un venue fuera
  de la banda de % en {BASE_LABEL} y más lejos del objetivo (bot status lo dice como
  inventory_band). Nunca compra para llegar al objetivo: reequilibrar es a mano.
  Si una pierna queda colgada, incierta o rechazada, el bot cancela lo abierto,
  no cubre nada y queda PARADO ("BOT ✖ PARADO" en rojo en la barra): revisa los
  saldos y rearma con bot on. bot status dice su estado y por qué no dispara;
  ctrl+b, p o bot off lo apagan.

RESULTADOS (tecla r o results)
  Ganancia o pérdida realizada de cada orden doble en live, del bot y manual,
  sacada del journal: lo cobrado en la venta menos lo pagado en la compra, menos
  las fees reales de los fills, sobre la cantidad que se llenó en las dos
  piernas. Lo llenado de más en una pierna sale como descuadre en {BASE_LABEL}, sin
  valorar. Las dobles colgadas o sin fills no suman. No es un PnL de cartera:
  no valora tu inventario ni los rebalanceos.

DIRECCIONES DE DEPÓSITO (tecla w o deposit)
  Dirección de BTC y de {BASE_LABEL} de cada venue con keys, pedida al venue cada vez
  que abres la ventana. Cada fila dice el ticker real del venue ({BASE_LABEL} es
  BTCB2): no mandes un activo a la dirección de otro. A la derecha, el QR de la
  fila seleccionada, para escanearla con el móvil. Enter o c copia la dirección
  (con xclip, xsel o wl-copy si están instalados; si no, solo por la terminal y
  se puede seleccionar con Shift + arrastrar); r vuelve a pedirlas. Solo se
  leen: la terminal no retira ni transfiere.

PLAN DE REEQUILIBRIO (tecla e o rebalance)
  Con tus saldos y el mid de cada venue, calcula UNA transferencia que deja los
  dos venues con el mismo % de {BASE_LABEL}: mandar {BASE_LABEL} del que tiene más % al
  otro, o BTC del que tiene menos % al otro. Para cada una: lo que hay que
  pedir en el formulario de retiro, la comisión real del venue (pedida al
  abrir), lo que sale y lo que llega (Neoxa cobra la comisión aparte), su coste
  en sats y el % que queda; si no se puede (disponible, mínimo, retiro cerrado),
  dice por qué. Recomienda la más barata y, con [bot], cuántas dobles la pagan.
  Una transferencia no cambia el % total: eso se corrige comprando o vendiendo.
  Solo calcula: la transferencia la haces tú en la web del exchange (las
  direcciones, con w). r vuelve a calcular.

[Esc] cerrar"""


class HelpScreen(ModalScreen[None]):
    """Ayuda (RF-15). Sin markup: lleva corchetes."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > VerticalScroll {
        width: 100; height: 40; border: thick $accent; background: $panel; padding: 0 1;
    }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("escape", "close", "cerrar"),
        Binding("question_mark", "close", show=False),
        Binding("q", "close", show=False),
    ]

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.text = help_text(mode)

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.text, markup=False)

    def action_close(self) -> None:
        self.dismiss(None)


class OrdersScreen(ModalScreen[None]):
    """Órdenes abiertas (RF-08), de un venue o de los dos. Se actualiza sola."""

    DEFAULT_CSS = """
    OrdersScreen { align: center middle; }
    OrdersScreen > Vertical {
        width: 130; height: 24; border: thick $accent; background: $panel; padding: 0 1;
    }
    OrdersScreen DataTable { height: 1fr; }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("escape", "close", "cerrar"),
        Binding("c", "cancel_selected", "cancelar la seleccionada"),
        Binding("x", "cancel_all", "cancelar todas"),
    ]

    def __init__(
        self,
        source: Callable[[], list[Order]],
        venue: VenueId | None,
        on_cancel: Callable[[CancelCommand], None],
    ) -> None:
        super().__init__()
        self._source = source
        self.venue = venue
        self._on_cancel = on_cancel
        self.order_ids: list[str] = []

    def compose(self) -> ComposeResult:
        where = VENUE_NAME[self.venue] if self.venue else "NonKYC y Neoxa"
        with Vertical():
            yield Static(
                f"Órdenes abiertas · {where} · [c] cancelar la seleccionada · "
                "[x] cancelar todas · [Esc] cerrar",
                markup=False,
            )
            yield DataTable(cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(
            "venue", "id", "lado", "cantidad", "precio", "llena", "estado", "client_id"
        )
        self.refresh_rows()
        table.focus()

    def refresh_rows(self) -> None:
        orders = [o for o in self._source() if self.venue in (None, o.venue)]
        table = self.query_one(DataTable)
        table.clear()
        for o in orders:
            table.add_row(
                VENUE_NAME[o.venue], o.order_id, o.side, f"{o.qty:f}", f"{o.price:f}",
                f"{o.filled:f}", o.status, o.client_id or "—", key=f"{o.venue}:{o.order_id}",
            )  # fmt: skip
        self.order_ids = [o.order_id for o in orders]

    def action_close(self) -> None:
        self.dismiss(None)

    def action_cancel_selected(self) -> None:
        table = self.query_one(DataTable)
        if not self.order_ids:
            return
        self._on_cancel(CancelOrder(self.order_ids[table.cursor_row]))

    def action_cancel_all(self) -> None:
        self._on_cancel(CancelVenue(self.venue) if self.venue else CancelAll())


RANGE_NAMES: dict[Range, str] = {"dia": "hoy (UTC)", "7d": "7 días", "todo": "todo"}
STATUS_NAMES = {"closed": "cerrada", "hanging": "COLGADA", "no_fills": "sin fills"}


def _sats(btc: Decimal | None, signed: bool = True) -> str:
    if btc is None:
        return "—"
    return f"{sats(btc):{'+' if signed else ''}.0f}"


def _pct(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:+.2f} %"


TAB_NAMES = {"doubles": "Dobles", "value": "Valor de la cuenta"}
RESULTS_EVERY_S = 60.0  # abierta, se vuelve a pedir el informe (RF-32, D-69)


class ResultsScreen(ModalScreen[None]):
    """Resultado realizado de las órdenes dobles (RF-25): gráficas, resumen y
    tabla. Se recalcula al abrirla, al cambiar de rango, con cada fill y cada
    `RESULTS_EVERY_S` mientras está abierta. En otra pestaña, el valor de la
    cuenta en el tiempo (RF-32)."""

    DEFAULT_CSS = """
    ResultsScreen { align: center middle; }
    ResultsScreen > Vertical {
        width: 156; height: 43; border: thick $accent; background: $panel; padding: 0 1;
    }
    ResultsScreen TabbedContent, ResultsScreen ContentSwitcher, ResultsScreen TabPane {
        height: 1fr;
    }
    ResultsScreen TabPane { padding: 0; }
    ResultsScreen Horizontal { height: 16; }
    ResultsScreen #value-charts { height: 1fr; }
    ResultsScreen ResultsChart, ResultsScreen ValueChart { width: 1fr; }
    ResultsScreen #results-summary { height: auto; margin: 0 0 1 0; }
    ResultsScreen #value-summary { height: auto; }
    ResultsScreen DataTable { height: 1fr; }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("escape", "close", "cerrar"),
        Binding("r", "close", show=False),
        Binding("1", "range('dia')", "hoy"),
        Binding("2", "range('7d')", "7 días"),
        Binding("3", "range('todo')", "todo"),
        Binding("tab", "next_tab", "pestaña", priority=True),
        Binding("z", "zoom", "último tramo"),
        Binding("shift+tab", "next_tab", show=False, priority=True),
    ]

    def __init__(self, source: RemoteResults, rng: Range, on_error: Callable[[str], None]) -> None:
        super().__init__()
        self._source = source
        self.range: Range = rng
        self._on_error = on_error
        self.report: Report | None = None
        self.zoom = False  # valor de la cuenta: solo el último tramo (D-69)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="results-title", markup=False)
            with TabbedContent(initial="doubles"):
                with TabPane(TAB_NAMES["doubles"], id="doubles"):
                    with Horizontal():
                        yield ResultsChart("cum", id="results-cum")
                        yield ResultsChart("bars", id="results-bars")
                    yield Static(id="results-summary", markup=False)
                    yield DataTable(cursor_type="row", zebra_stripes=True)
                with TabPane(TAB_NAMES["value"], id="value"):
                    with Horizontal(id="value-charts"):
                        yield ValueChart("total", id="value-total")
                        yield ValueChart("venues", id="value-venues")
                    yield Static(id="value-summary", markup=False)

    @property
    def tab(self) -> str:
        return self.query_one(TabbedContent).active

    def action_next_tab(self) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = "value" if tabs.active == "doubles" else "doubles"
        if tabs.active == "doubles":
            self.query_one(DataTable).focus()
        self._draw_title()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._draw_title()  # también con el ratón

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "inicio UTC", "doble", "origen", "ruta", "casada", "compra", "venta",
            "fees sats", "esperada", "real", "resultado sats", "descuadre", "estado",
        )  # fmt: skip
        self.reload()
        self.refresh_report()
        self.query_one(DataTable).focus()
        # el daemon sigue apuntando muestras y fills: abierta no se congela (D-69)
        self.set_interval(RESULTS_EVERY_S, self.refresh_report)

    def refresh_report(self) -> None:
        """Pide lo que falte (precio del token, D-65; con daemon, el informe, D-66)
        en segundo plano y redibuja."""

        async def work() -> None:
            if error := await self._source.refresh(self.range):
                self._on_error(f"resultados: {error}")
            self.reload()

        self.run_worker(work(), group="results-price", exclusive=True)

    def reload(self) -> None:
        try:
            report = self._source.load(self.range)
        except OSError as exc:
            self._on_error(f"resultados: no se pudo leer el journal ({exc})")
            return
        if report == self.report:
            return  # sin cambios: ni se redibuja ni se mueve la selección
        self.report = report
        self._draw()

    def _draw(self) -> None:
        if self.report is None:
            return
        self._draw_title()
        shown = self._shown_values()
        self.query_one("#value-total", ValueChart).show(shown, relative=self.zoom)
        self.query_one("#value-venues", ValueChart).show(shown, relative=self.zoom)
        self.query_one("#value-summary", Static).update(self.value_text())
        self.query_one("#results-cum", ResultsChart).show(self.report.doubles)
        self.query_one("#results-bars", ResultsChart).show(self.report.doubles)
        self.query_one("#results-summary", Static).update(self.summary_text())
        table = self.query_one(DataTable)
        selected = None  # la fila seleccionada sigue en su doble al redibujar
        if table.row_count:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear()
        priced = {a for a, _ in self.report.summary.prices}
        for d in reversed(self.report.doubles):  # la más reciente arriba
            table.add_row(*self._cells(d, priced), key=d.id)
        if selected is not None and selected in table.rows:
            table.move_cursor(row=table.get_row_index(selected), animate=False)

    @staticmethod
    def _cells(d: DoubleResult, priced: Collection[str] = ()) -> tuple[str, ...]:
        """`priced`: tokens cuya fee ya va en sats (D-65)."""
        started = datetime.fromtimestamp(d.started / 1000, UTC).strftime("%m-%d %H:%M:%S")
        avg = lambda v: "—" if v is None else f"{v:.8f}"  # noqa: E731
        fees = _sats(d.fees + d.token_fees, signed=False)
        valued = [f"{q:f} {a}" for a, q in d.other_fees if a in priced]
        loose = [f"{q:f} {a}" for a, q in d.other_fees if a not in priced]
        if valued:  # ya en sats (D-65): la cantidad, para saber de dónde salen
            fees += f" ({', '.join(valued)})"
        if loose:
            fees += " + " + ", ".join(loose)
        return (
            started, d.id, d.origin, f"{d.buy_venue or '?'}→{d.sell_venue or '?'}",
            f"{d.matched:f}", avg(d.buy_avg), avg(d.sell_avg), fees,
            _pct(d.expected_pct), _pct(d.result_pct), _sats(d.result),
            "—" if d.mismatch == 0 else f"{d.mismatch:+f}", STATUS_NAMES[d.status],
        )  # fmt: skip

    def summary_text(self) -> str:
        if self.report is None:
            return ""
        s = self.report.summary
        parts = [f"{s.closed} dobles cerradas"]
        if s.closed:
            parts += [
                f"total {s.total:+.8f} BTC · {_sats(s.total)} sats",
                f"media {_sats(s.mean)} · mejor {_sats(s.best)} · peor {_sats(s.worst)} sats",
            ]
        if s.prices:  # RF-25, D-65: fees en token restadas a su precio de ahora
            used = ", ".join(f"{a} a {p:.8f} BTC" for a, p in s.prices)
            parts.append(f"fees en token restadas con {used} (precio de ahora)")
        if s.other_fees:  # sin precio: no se valoran en BTC
            other = ", ".join(f"{q:f} {a}" for a, q in s.other_fees)
            parts.append(f"+ {other} en fees, sin descontar del total")
        extra = [
            (s.hanging, "colgadas (no suman)"),
            (s.no_fills, "sin fills"),
            (s.unassigned, "fills sin asignar a una orden de la terminal"),
            (s.bad_rows, "líneas del journal ilegibles"),
        ]
        parts += [f"{n} {what}" for n, what in extra if n]
        return " · ".join(parts)

    def _shown_values(self) -> tuple[ValueSample, ...]:
        values = self.report.values if self.report is not None else ()
        return last_stretch(values) if self.zoom else values

    def action_zoom(self) -> None:
        self.zoom = not self.zoom
        if self.report is not None:
            self._draw()

    def _draw_title(self) -> None:
        if self.tab == "doubles":
            what, zoom = "Resultados de las órdenes dobles", ""
        else:
            what = "Valor de la cuenta" + (" · último tramo" if self.zoom else "")
            zoom = "[z] rango entero  ·  " if self.zoom else "[z] último tramo  ·  "
        self.query_one("#results-title", Static).update(
            f"{what} · {RANGE_NAMES[self.range]} · "
            f"[1] hoy  [2] 7 días  [3] todo  ·  {zoom}[Tab] pestaña  ·  [Esc] cerrar"
        )

    def value_text(self) -> str:
        """Resumen de la serie (RF-32) y lo que no es: depósitos y retiros sin
        ajustar, huecos sin daemon."""
        values = self.report.values if self.report is not None else ()
        s = summarize(values)
        if s is None:
            return (
                "aún sin muestras en el rango: el daemon apunta una cada `value_every_s` "
                "(5 min por defecto) si tiene saldos y mid de los dos venues"
            )
        fmt = "%m-%d %H:%M"
        since = datetime.fromtimestamp(s.since / 1000, UTC).strftime(fmt)
        until = datetime.fromtimestamp(s.until / 1000, UTC).strftime(fmt)
        pct = f" ({s.change_pct:+.2f} %)" if s.change_pct is not None else ""
        venues = " · ".join(
            f"{VENUE_NAME.get(v.venue, v.venue)} {v.value:.8f}" for v in values[-1].venues
        )
        return "\n".join([
            f"ahora {s.last:.8f} BTC ({venues}) · cambio {s.change:+.8f} BTC{pct} "
            f"desde {s.first:.8f}",
            f"máx {s.high:.8f} · mín {s.low:.8f} · {s.count} muestras de {since} a {until} UTC",
            *self._stretch_text(),
            "Los depósitos y retiros salen como saltos: es el valor, no es PnL. "
            "Los huecos son ratos sin daemon. Valor = BTC + BTCB2 × mid de cada venue.",
        ])  # fmt: skip

    def _stretch_text(self) -> list[str]:
        """Con `z`: de cuándo es el tramo y cuánto cambió en sats."""
        if not self.zoom:
            return []
        s = summarize(self._shown_values())
        if s is None:
            return []
        since = datetime.fromtimestamp(s.since / 1000, UTC).strftime("%m-%d %H:%M")
        return [
            f"último tramo desde {since} UTC (tras el último salto > 5 %): "
            f"cambio {_sats(s.change)} sats en {s.count} muestras"
        ]

    def row_count(self) -> int:
        return self.query_one(DataTable).row_count

    def action_range(self, rng: Range) -> None:
        self.range = rng
        self.reload()
        self.refresh_report()

    def action_close(self) -> None:
        self.dismiss(None)


class DepositScreen(ModalScreen[None]):
    """Direcciones de depósito de BTC y BTCB2 por venue (RF-28). Se piden al
    venue al abrirla y con `r`; una fila con error nunca muestra dirección.
    A la derecha, el QR de la fila resaltada para pagar desde el móvil (D-52)."""

    DEFAULT_CSS = """
    DepositScreen { align: center middle; }
    DepositScreen > Vertical {
        width: 150; height: 25; max-height: 100%;
        border: thick $accent; background: $panel; padding: 0 1;
    }
    DepositScreen Horizontal { height: 1fr; }
    DepositScreen DataTable { height: 1fr; width: 1fr; }
    DepositScreen #deposit-qr { width: 35; height: auto; padding: 0 1; }
    DepositScreen #deposit-status { height: auto; }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("escape", "close", "cerrar"),
        Binding("w", "close", show=False),
        Binding("c", "copy", "copiar"),
        Binding("r", "reload", "volver a pedir"),
    ]

    def __init__(
        self, source: RemoteDeposits, copy: Callable[[str], Awaitable[str | None]]
    ) -> None:
        super().__init__()
        self._source = source
        self._copy = copy  # devuelve la herramienta del sistema que copió, o None
        self.rows: list[DepositRow] | None = None  # None: aún no han llegado
        self._qr = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "Direcciones de depósito · [Enter/c] copiar · [r] volver a pedir · [Esc] cerrar\n"
                f"{BASE_LABEL} es el ticker BTCB2 de los venues: no mandes un activo a la "
                "dirección de otro.",
                markup=False,
            )
            with Horizontal():
                yield DataTable(cursor_type="row", zebra_stripes=True)
                yield Static(id="deposit-qr", markup=False)
            yield Static(id="deposit-status", markup=False)

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "venue", "activo", "red", "dirección", "mín.", "conf."
        )
        self.query_one(DataTable).focus()
        self.action_reload()

    def action_reload(self) -> None:
        self.rows = None
        self._status("pidiendo direcciones a los venues…")
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        rows = await self._source.load()
        table = self.query_one(DataTable)
        table.clear()
        for r in rows:
            table.add_row(*self._cells(r))
        self.rows = rows
        failed = sum(r.address is None for r in rows)
        self._status(f"{failed} sin dirección" if failed else "")
        self._show_qr(table.cursor_row)

    @staticmethod
    def _cells(r: DepositRow) -> tuple[str | Text, ...]:
        label = asset_label(r.asset)
        asset = f"{label} ({r.asset})" if label != r.asset else r.asset
        a = r.address
        if a is None:
            return VENUE_NAME[r.venue], asset, "—", Text(f"⚠ {r.error}", style="yellow"), "—", "—"
        network = (
            a.network if a.ticker.lower() == a.network.lower() else f"{a.network} · {a.ticker}"
        )
        address = a.address + (f"  (memo {a.memo})" if a.memo else "")
        minimum = "—" if a.min_deposit is None else f"{a.min_deposit:f}"
        confs = "—" if a.confirmations is None else str(a.confirmations)
        return VENUE_NAME[r.venue], asset, network, address, minimum, confs

    def table_text(self) -> str:
        """Filas en texto plano (tests y depuración)."""
        table = self.query_one(DataTable)
        return "\n".join(
            " | ".join(str(c) for c in table.get_row_at(i)) for i in range(table.row_count)
        )

    def status_text(self) -> str:
        return str(self.query_one("#deposit-status", Static).render())

    def qr_text(self) -> str:
        """El QR (o su aviso) en texto plano (tests)."""
        return self._qr

    def _status(self, text: str) -> None:
        self.query_one("#deposit-status", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_qr(event.cursor_row)

    def _show_qr(self, index: int) -> None:
        """Solo la dirección, sin `bitcoin:`: BTCB2 no es bitcoin (D-52)."""
        if not self.rows or not 0 <= index < len(self.rows):
            return
        r = self.rows[index]
        widget = self.query_one("#deposit-qr", Static)
        if r.address is None:
            self._qr = "sin QR: la fila no tiene dirección"
            widget.update(self._qr)
            return
        self._qr = "\n".join(qr_lines(r.address.address))
        caption = f"\n{asset_label(r.asset)} en {VENUE_NAME[r.venue]} ({r.address.ticker})"
        widget.update(Text(self._qr, style="#ffffff on #000000") + Text(caption))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_copy()

    def action_copy(self) -> None:
        if not self.rows:
            return
        r = self.rows[self.query_one(DataTable).cursor_row]
        where = f"{asset_label(r.asset)} en {VENUE_NAME[r.venue]}"
        if r.address is None:
            self._status(f"{where}: sin dirección, no se copia nada")
            return
        self.run_worker(self._copy_address(where, r.address), group="copy")

    async def _copy_address(self, where: str, a: DepositAddress) -> None:
        tool = await self._copy(a.address)
        if tool is not None:
            self._status(f"copiada con {tool} la dirección de {where} ({a.ticker}): {a.address}")
        else:
            self._status(
                f"enviada solo a la terminal (OSC 52), que puede ignorarla; si no se pega, "
                f"selecciónala con Shift + arrastrar · {where} ({a.ticker}): {a.address}"
            )

    def action_close(self) -> None:
        self.dismiss(None)


def _sats_text(btc: Decimal | None) -> str:
    return "—" if btc is None else f"{sats(btc):,.0f} sats".replace(",", " ")


def _pct1(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.1f} %"


class RebalanceScreen(ModalScreen[None]):
    """Plan de reequilibrio (RF-29): dos alternativas de una transferencia que
    igualan el % de BTCB2 de los venues, con las comisiones pedidas al abrir y
    con `r`. Solo calcula (D-53)."""

    DEFAULT_CSS = """
    RebalanceScreen { align: center middle; }
    RebalanceScreen > Vertical {
        width: 150; height: 26; max-height: 100%;
        border: thick $accent; background: $panel; padding: 0 1;
    }
    RebalanceScreen DataTable { height: 5; }
    RebalanceScreen Static { height: auto; }
    """
    BINDINGS = [  # noqa: RUF012  (así lo declara Textual)
        Binding("escape", "close", "cerrar"),
        Binding("e", "close", show=False),
        Binding("r", "reload", "volver a calcular"),
    ]

    def __init__(
        self,
        source: RemoteRebalance,
        holdings: Callable[[], Mapping[VenueId, Holding] | str],
        target: InventoryTarget | None = None,
        limits: BotLimits | None = None,
    ) -> None:
        super().__init__()
        self._source = source
        self._holdings = holdings
        self._target = target
        self._limits = limits
        self.plan: Plan | None = None  # None: aún no hay plan

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "Plan de reequilibrio · [r] volver a calcular · [Esc] cerrar\n"
                "Solo calcula: la terminal no retira ni transfiere. La transferencia la "
                "haces tú en la web del exchange (direcciones con w).",
                id="rebalance-head",
                markup=False,
            )
            yield Static(id="rebalance-state", markup=False)
            yield DataTable(cursor_type="row", zebra_stripes=True)
            yield Static(id="rebalance-notes", markup=False)

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "envío", "pedir", "comisión", "sale", "llega", "coste", "% después", "estado"
        )
        self.action_reload()

    def action_reload(self) -> None:
        self.plan = None
        self._show("rebalance-notes", "pidiendo las comisiones de retiro a los venues…")
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        if isinstance(self._holdings(), str):
            self._fail(str(self._holdings()))
            return
        fees = await self._source.load()
        held = self._holdings()  # los saldos de ahora, no los de antes de pedir
        result = held if isinstance(held, str) else plan(held, fees)
        if isinstance(result, str):
            self._fail(result)
            return
        self._show("rebalance-state", self._state(result))
        table = self.query_one(DataTable)
        table.clear()
        for t in result.transfers:
            table.add_row(*self._cells(t))
        self._show("rebalance-notes", self._notes(result, held))
        self.plan = result

    def _fail(self, why: str) -> None:
        self.query_one(DataTable).clear()
        self._show("rebalance-state", "")
        self._show("rebalance-notes", f"⚠ no hay plan: {why}")

    @staticmethod
    def _state(p: Plan) -> str:
        venues = " · ".join(f"{VENUE_NAME[v]} {_pct1(pct)}" for v, pct in p.before.items())
        return f"{BASE_LABEL} por venue ahora: {venues} · total {_pct1(p.total_pct)}"

    @staticmethod
    def _route(t: Transfer) -> str:
        return f"{asset_label(t.asset)} {VENUE_NAME[t.source]} → {VENUE_NAME[t.dest]}"

    @classmethod
    def _cells(cls, t: Transfer) -> tuple[str | Text, ...]:
        fee = "—" if t.fee is None else f"{t.fee:f}"
        after = " · ".join(f"{VENUE_NAME[v]} {_pct1(pct)}" for v, pct in t.after.items())
        state = Text("viable", style="green") if t.viable else Text(f"⚠ {t.problem}", "yellow")
        return (
            cls._route(t), f"{t.amount:f}", fee, f"{t.debited:f}", f"{t.received:f}",
            _sats_text(t.cost_btc), after, state,
        )  # fmt: skip

    def _notes(self, p: Plan, held: Mapping[VenueId, Holding]) -> str:
        best = p.best
        if best is None:
            lines = ["ninguna alternativa es viable ahora"]
        else:
            lines = [
                f"Recomendada: {self._route(best)}, pedir {best.amount:f} "
                f"{asset_label(best.asset)} en el retiro (salen {best.debited:f}, llegan "
                f"{best.received:f}); comisión {_sats_text(best.cost_btc)}"
            ]
            if self._limits is not None and best.cost_btc is not None:
                lim = self._limits
                price = held[best.source].price
                n = break_even(best.cost_btc, lim.max_qty, price, lim.min_net_pct)
                if n is not None:
                    lines.append(
                        f"la pagan ≈ {n} dobles del bot de {lim.max_qty:f} {BASE_LABEL} "
                        f"a la neta mínima ({lim.min_net_pct} %)"
                    )
        t = self._target
        if t is not None and p.total_pct is not None and not t.in_band(p.total_pct):
            lines.append(
                f"⚠ el total ({_pct1(p.total_pct)}) está fuera de la banda {t.low}-{t.high} %: "
                "una transferencia no lo cambia; se corrige comprando o vendiendo a mano"
            )
        lines.append(
            "«pedir» es lo que se escribe en el formulario de retiro. Neoxa cobra la "
            "comisión aparte; en NonKYC se supone descontada de lo pedido (D-53)."
        )
        return "\n".join(lines)

    def _show(self, widget: str, text: str) -> None:
        self.query_one(f"#{widget}", Static).update(text)

    def text(self) -> str:
        """Todo en texto plano (tests y depuración)."""
        table = self.query_one(DataTable)
        rows = [" | ".join(str(c) for c in table.get_row_at(i)) for i in range(table.row_count)]
        parts = [str(self.query_one(f"#rebalance-{w}", Static).render()) for w in ("head", "state")]
        parts += rows
        parts.append(str(self.query_one("#rebalance-notes", Static).render()))
        return "\n".join(parts)

    def action_close(self) -> None:
        self.dismiss(None)


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
        book_levels: int,
        timeframe: Timeframe,
        mode: str,
        can_trade: Mapping[VenueId, bool] | None = None,
        orders: RemoteOrders | None = None,
        orders_note: str = "",
        account: RemoteAccount | None = None,
        run_hub: bool = True,
        clock: Callable[[], int] | None = None,
        startup_warnings: Sequence[str] = (),
        bot: RemoteBot | None = None,
        fee_discounts: Mapping[VenueId, Decimal] | None = None,
        results: RemoteResults | None = None,
        deposits: RemoteDeposits | None = None,
        inventory_target: InventoryTarget | None = None,
        clipboard: Clipboard | None = None,
        rebalance: RemoteRebalance | None = None,
        bot_limits: BotLimits | None = None,
        engine: Runner,
        remote: RemoteEngine | None = None,
    ) -> None:
        super().__init__()
        # TUI conectada al daemon (RF-31): mira y solo arma o para el bot
        self.remote = remote
        self.hub = hub
        self.book_levels = book_levels
        self.timeframe: Timeframe = timeframe
        self.mode = mode
        self._run_hub = run_hub
        self.can_trade = dict(can_trade or {})
        self.orders = orders
        self._orders_note = orders_note
        self.account = account
        self.bot = bot
        self.results = results
        self.deposits = deposits  # solo venues con keys; None si no hay ninguno (RF-28)
        self.inventory_target = inventory_target  # ratio de inventario (RF-27)
        self.rebalance = rebalance  # comisiones de retiro; None sin keys (RF-29)
        self.bot_limits = bot_limits  # para las dobles que pagan el reequilibrio
        # copia con wl-copy/xclip/xsel además de OSC 52; None en tests (D-52)
        self.system_clipboard = clipboard
        # descuento de fee por venue, solo para el panel cruzado (RF-24, D-38)
        self._fee_discounts = dict(fee_discounts or {})
        self.last_message = ""
        self.last_error = False
        self._clock = clock or hub.clock  # el del hub: ui no importa adapters (D-54)
        self.startup_warnings = tuple(startup_warnings)
        self._dirty_books: set[VenueId] = set()
        self._dirty_charts: set[VenueId] = set()
        self._shown_double: str | None = None
        # avisos, fallos y bucles que no dependen de la pantalla (D-61, D-66)
        self.engine = engine
        self._faults = self.engine.faults

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
                    note=self._orders_note,
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
        for faults in self._faults:
            faults.subscribe(self._on_fault)
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
        faults = sum(f.count for f in self._faults)
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
        except (DaemonGone, DaemonRefused) as exc:
            self.say(str(exc), error=True)
        except Exception as exc:
            self._faults[0].report(where, exc)

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
        except (DaemonGone, DaemonRefused) as exc:  # conectada: el daemon no está o no quiso
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
