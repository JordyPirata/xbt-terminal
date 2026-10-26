"""Paneles de la pantalla: book L2, velas, barra inferior y línea de comandos.

El book va en escalera horizontal (compras a la izquierda, ventas a la derecha)
para que los N niveles de RF-02 quepan en 160×45 junto a los gráficos. Las velas
convierten a float solo para dibujar: los precios que se muestran en texto salen
del Decimal (RNF-07).

El panel de piernas de la orden doble (T-18) solo aparece tras la primera. El de
balances (T-19) solo cuando hay cuenta (dry-run o live).

Spec: RF-02, RF-03, RF-04, RF-05, RF-07, RNF-02, RNF-06 · design.md §6.4, §6.6, §9
Tareas: T-13, T-14b, T-18, T-19
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from rich.text import Text
from textual import events
from textual.app import RenderResult
from textual.message import Message
from textual.widgets import Input, Static
from textual_plotext import PlotextPlot

from ..core.book import BookRow, L2Book
from ..core.cross import Quote, Route, VenueStats, cross, sats, venue_stats
from ..core.double import DoubleState, Leg
from ..core.inventory import Inventory, InventoryTarget
from ..core.models import BASE_LABEL, ZERO, Balance, Candle, MarketSpec, Timeframe, VenueId
from ..core.results import DoubleResult
from ..core.valuation import ValueSample, segments, thin

TITLES: dict[VenueId, str] = {
    "nk": f"NonKYC {BASE_LABEL}/BTC",
    "nx": f"Neoxa {BASE_LABEL}/BTC",
}
HUNDRED = Decimal(100)
# Ancho de una fila: 2 × (12 + 12 + 11 + 2) + 3 = 77, cabe en media pantalla
# (80 columnas menos los 2 del borde)
QTY_W, PRICE_W = 12, 11
AMM_TAG = " A"  # marca de nivel AMM; la leyenda va en el borde
SIDE_W = 2 * QTY_W + PRICE_W + len(AMM_TAG)  # ancho de cada lado de la escalera
SEP = " │ "
LADDER_W = 2 * SIDE_W + len(SEP)  # escalera entera, sin barras


def depth_bar(fraction: Decimal, width: int, side: str) -> str:
    """Barra de profundidad de `width` columnas llena en `fraction` (0-1), con
    medias celdas. Las compras crecen hacia la izquierda y las ventas hacia la
    derecha, las dos desde la escalera; un nivel con algo ocupa media celda."""
    halves = round(fraction * width * 2)
    if fraction > 0 and width:
        halves = max(halves, 1)
    full, half = divmod(halves, 2)
    if side == "bid":
        return (("▐" if half else "") + "█" * full).rjust(width)
    return ("█" * full + ("▌" if half else "")).ljust(width)


def _decimals(step: Decimal) -> int:
    return max(0, -int(step.as_tuple().exponent))


class BookPanel(Static):
    """Book L2 en escalera centrada, con la profundidad acumulada en barras a
    los lados (D-49). Un clic en un nivel publica `Picked` (RF-16)."""

    DEFAULT_CSS = """
    BookPanel { border: round $primary; padding: 0; }
    """

    class Picked(Message):
        """Nivel elegido: precio y cantidad (la del nivel o la acumulada)."""

        def __init__(
            self, venue: VenueId, side: str, price: Decimal, qty: Decimal, decimals: tuple[int, int]
        ) -> None:
            super().__init__()
            self.venue, self.side, self.price, self.qty = venue, side, price, qty
            self.price_decimals, self.qty_decimals = decimals

    def __init__(self, venue: VenueId, levels: int) -> None:
        super().__init__(id=f"book-{venue}")
        self.venue = venue
        self.levels = levels
        self.text = ""  # última versión en texto plano (tests y depuración)
        self.border_title = f"Book · {TITLES[venue]}"
        if venue == "nx":
            self.border_subtitle = f"{AMM_TAG.strip()} = liquidez AMM del pool"
        self.styles.height = levels + 4  # cabecera, niveles, resumen y bordes
        self._rows: tuple[tuple[BookRow, ...], tuple[BookRow, ...]] = ((), ())
        self._decimals = (8, 8)  # (precio, cantidad) del último book pintado
        self._last: tuple[L2Book | None, MarketSpec | None] = (None, None)
        self.bar_width = 0  # columnas de cada barra de profundidad
        self.ladder_x = 0  # columna de contenido donde empieza la escalera

    def on_mount(self) -> None:
        self.show(None, None)

    def level_at(self, x: int, y: int) -> tuple[str, Decimal, Decimal] | None:
        """(lado, precio, cantidad) del nivel en (x, y) de contenido; None fuera de
        un nivel. Fila 0: cabecera; 1..levels: niveles; a la izquierda del
        separador, bid. En la columna de acumulado la cantidad es la acumulada."""
        if not 1 <= y <= self.levels:
            return None
        bids, asks = self._rows
        x -= self.ladder_x  # las barras quedan en negativo (compras) o tras las ventas
        ask_x = SIDE_W + len(SEP)
        if -self.ladder_x <= x < SIDE_W:
            side, rows, cumulative = "bid", bids, x < QTY_W
        elif x >= ask_x:
            side, rows, cumulative = "ask", asks, x >= ask_x + PRICE_W + QTY_W
        else:
            return None
        i = y - 1
        if i >= len(rows):
            return None
        row = rows[i]
        return side, row.price, row.cumulative if cumulative else row.qty

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None:
            return
        hit = self.level_at(offset.x, offset.y)
        if hit is not None:
            self.post_message(self.Picked(self.venue, *hit, self._decimals))

    def on_resize(self) -> None:
        self.show(*self._last)

    def show(self, book: L2Book | None, spec: MarketSpec | None) -> None:
        self._last = (book, spec)
        text = self._render_book(book, spec)
        self.text = text.plain
        self.update(text)

    def _render_book(self, book: L2Book | None, spec: MarketSpec | None) -> Text:
        if book is None or spec is None or not book.valid:
            self._rows = ((), ())
            return Text("esperando snapshot del book…", style="dim italic")
        qd = _decimals(spec.lot_size)
        pd = _decimals(spec.tick_size)
        bids, asks = book.top(self.levels)
        self._rows, self._decimals = (bids, asks), (pd, qd)
        # lo que sobra a cada lado de la escalera es barra, con 1 columna de hueco
        width = self.content_size.width
        bar_w = max(0, (width - LADDER_W) // 2 - 1)
        side_w = bar_w + 1 if bar_w else 0
        self.bar_width = bar_w
        self.ladder_x = max(0, (width - LADDER_W - 2 * side_w) // 2) + side_w
        margin = " " * (self.ladder_x - side_w)
        depth = max((rows[-1].cumulative for rows in (bids, asks) if rows), default=ZERO)
        out = Text()
        pad = " " * len(AMM_TAG)
        head = f"{'acumulado':>{QTY_W}}{'cantidad':>{QTY_W}}{'compra':>{PRICE_W}}{pad}"
        head += f" │ {'venta':<{PRICE_W}}{'cantidad':>{QTY_W}}{'acumulado':>{QTY_W}}{pad}"
        out.append(" " * self.ladder_x + head + "\n", style="bold")
        for i in range(self.levels):
            bid = bids[i] if i < len(bids) else None
            ask = asks[i] if i < len(asks) else None
            out.append(margin)
            if bar_w:
                out.append_text(self._bar(bid, depth, bar_w, "bid"))
                out.append(" ")
            out.append_text(self._side(bid, qd, pd, "bid"))
            out.append(SEP)
            out.append_text(self._side(ask, qd, pd, "ask"))
            if bar_w:
                out.append(" ")
                out.append_text(self._bar(ask, depth, bar_w, "ask"))
            out.append("\n")
        summary = self._summary(book, pd)
        out.append(" " * max(0, (width - len(summary)) // 2))
        out.append_text(summary)
        return out

    @staticmethod
    def _bar(row: BookRow | None, depth: Decimal, width: int, side: str) -> Text:
        """Barra del acumulado del nivel respecto al más profundo de los dos lados."""
        if row is None or depth <= 0:
            return Text(" " * width)
        style = "green" if side == "bid" else "red"
        return Text(depth_bar(row.cumulative / depth, width, side), style=style)

    @staticmethod
    def _side(row: BookRow | None, qd: int, pd: int, side: str) -> Text:
        if row is None:
            return Text(" " * SIDE_W)
        price = f"{row.price:.{pd}f}"
        qty, cum = f"{row.qty:.{qd}f}", f"{row.cumulative:.{qd}f}"
        tag = AMM_TAG if row.amm else " " * len(AMM_TAG)
        if side == "bid":
            line = f"{cum:>{QTY_W}}{qty:>{QTY_W}}{price:>{PRICE_W}}{tag}"
            style = "green"
        else:
            line = f"{price:<{PRICE_W}}{qty:>{QTY_W}}{cum:>{QTY_W}}{tag}"
            style = "red"
        return Text(line, style=f"{style} dim" if row.amm else style)

    @staticmethod
    def _summary(book: L2Book, pd: int) -> Text:
        bid, ask, spread, mid = book.best_bid, book.best_ask, book.spread, book.mid
        if spread is None or mid is None:
            return Text("sin spread: falta un lado o el book está cruzado", style="yellow")
        pct = spread / mid * HUNDRED
        return Text(
            f"bid {bid:.{pd}f} · ask {ask:.{pd}f} · spread {spread:.{pd}f} "
            f"({sats(spread):.0f} sats · {pct:.2f} %)",
            style="bold",
        )


MAX_STEP = 4  # columnas por vela como mucho, para que pocas velas no parezcan sueltas
Y_TICKS = 6


def candle_layout(count: int, canvas: int) -> tuple[int, int]:
    """(velas que se dibujan, columnas por vela) para `canvas` columnas (D-24).

    El paso es entero e igual para todas: si plotext reparte N velas en M
    columnas con M no múltiplo de N, deja columnas vacías sueltas."""
    shown = min(count, max(canvas, 0))
    if shown == 0:
        return 0, 1
    return shown, max(1, min(MAX_STEP, canvas // shown))


def _price_ticks(low: float, high: float) -> tuple[list[float], list[str]]:
    """Marcas del eje Y con el mismo ancho, para saber cuánto ocupa (D-24)."""
    if high <= low:
        high = low + abs(low) * 0.01 or 1.0
    gap = (high - low) / (Y_TICKS - 1)
    decimals = max(0, 1 - math.floor(math.log10(gap)))
    ticks = [low + gap * i for i in range(Y_TICKS)]
    return ticks, [f"{t:.{decimals}f}" for t in ticks]


class ChartPanel(PlotextPlot):
    DEFAULT_CSS = """
    ChartPanel { border: round $secondary; }
    """

    def __init__(self, venue: VenueId) -> None:
        super().__init__(id=f"chart-{venue}")
        self.venue = venue
        self.timeframe: Timeframe = "1m"
        self.candles: tuple[Candle, ...] = ()
        self.border_title = f"Velas · {TITLES[venue]}"

    def show(self, candles: tuple[Candle, ...], timeframe: Timeframe) -> None:
        """Guarda las velas; cuántas caben depende del ancho y se ve al pintar."""
        self.candles = candles
        self.timeframe = timeframe
        self.border_subtitle = timeframe
        self.refresh()

    def render(self) -> RenderResult:
        self._draw(self.size.width)
        return super().render()

    def _draw(self, width: int) -> None:
        plt = self.plt
        plt.clear_data()
        if not self.candles:
            plt.title("cargando histórico…")
            return
        # El ancho de las etiquetas del eje Y depende del rango, y el del área de
        # dibujo de ellas: se calcula con todas y se recalcula con las que caben
        ticks, labels = _price_ticks(*self._range(self.candles))
        canvas = width - 2 - max(map(len, labels))  # 2 = los dos bordes del marco
        shown, step = candle_layout(len(self.candles), canvas)
        candles = self.candles[len(self.candles) - shown :]
        ticks, labels = _price_ticks(*self._range(candles))
        canvas = width - 2 - max(map(len, labels))
        shown, step = candle_layout(len(candles), canvas)
        candles = candles[len(candles) - shown :]
        if not candles:
            return
        xs = list(range(len(candles)))
        plt.candlestick(
            xs,
            {
                "Open": [float(c.open) for c in candles],
                "Close": [float(c.close) for c in candles],
                "High": [float(c.high) for c in candles],
                "Low": [float(c.low) for c in candles],
            },
        )
        # Con este rango, plotext pone la vela i en la columna canvas-1-step·(n-1-i)
        plt.xlim(xs[-1] - (canvas - 1) / step, xs[-1])
        plt.ylim(ticks[0], ticks[-1])
        plt.yticks(ticks, labels)
        every = max(1, len(xs) // 6)
        fmt = axis_format(self.timeframe)
        plt.xticks(
            xs[::every],
            [
                datetime.fromtimestamp(candles[i].ts_open / 1000, UTC).strftime(fmt)
                for i in xs[::every]
            ],
        )
        plt.title(f"{self.timeframe} · último {candles[-1].close} BTC · hora UTC")

    @staticmethod
    def _range(candles: tuple[Candle, ...]) -> tuple[float, float]:
        """Rango de los cuerpos, no de las mechas: una mecha suelta aplastaría
        todas las velas; la que se sale queda cortada en el borde (D-67)."""
        bodies = [p for c in candles for p in (c.open, c.close)]
        return float(min(bodies)), float(max(bodies))


NAMES: dict[VenueId, str] = {"nk": "NonKYC", "nx": "Neoxa"}
DASH = "—"


def axis_format(timeframe: Timeframe) -> str:
    """Etiquetas del eje X: con fecha cuando la hora sola se repite (D-23)."""
    return {"1h": "%d %H:%M", "4h": "%d %H:%M", "1d": "%m-%d"}.get(timeframe, "%H:%M")


def _fmt(value: Decimal, pct: Decimal, signed: bool) -> str:
    """precio · sats · %, con los % que ya calcula `core/cross.py`."""
    sign = "+" if signed else ""
    return f"{value:{sign}.8f} · {sats(value):{sign}.0f} sats · {pct:{sign}.2f} %"


def _stats_line(venue: VenueId, stats: VenueStats | None) -> str:
    if stats is None:
        return f"{NAMES[venue]:<7} mid {DASH}  spread {DASH}"
    spread = _fmt(stats.spread, stats.spread_pct, signed=False)
    return f"{NAMES[venue]:<7} mid {stats.mid:.8f}  spread {spread}"


def _route_text(buy: VenueId, sell: VenueId, route: Route | None) -> Text:
    out = Text(f"{buy}→{sell}  ")
    if route is None:
        out.append(f"bruto {DASH}  neto {DASH}", style="dim")
        return out
    out.append(f"bruto {_fmt(route.gross, route.gross_pct, signed=True)}  ")
    style = "bold green" if route.net > 0 else "dim red"
    out.append(f"neto {_fmt(route.net, route.net_pct, signed=True)}", style=style)
    return out


def render_cross(nk: Quote | None, nx: Quote | None, note: str = "") -> Text:
    """Tres líneas: stats de cada venue a la izquierda y las dos rutas a la
    derecha; abajo la diferencia de mids. Lo que no se puede calcular sale "—"."""
    stats = {"nk": venue_stats(nk) if nk else None, "nx": venue_stats(nx) if nx else None}
    view = cross(nk, nx) if nk and nx else None
    left = [_stats_line("nk", stats["nk"]), _stats_line("nx", stats["nx"])]
    width = max(len(ln) for ln in left)
    out = Text()
    for (buy, sell), text in zip((("nk", "nx"), ("nx", "nk")), left, strict=True):
        route = view.route(buy, sell) if view else None
        out.append(f"{text:<{width}} │ ")
        out.append_text(_route_text(buy, sell, route))
        out.append("\n")
    if view and view.mid_diff is not None and view.mid_diff_pct is not None:
        diff = _fmt(view.mid_diff, view.mid_diff_pct, signed=True)
    else:
        diff = DASH
    out.append(f"nx - nk  {diff}", style="bold")
    out.append(
        "   · ruta = comprar en el primero y vender en el segundo; neto con fee taker"
        + (f" ({note})" if note else ""),
        "dim",
    )
    return out


class CrossPanel(Static):
    """Panel cruzado (RF-04)."""

    DEFAULT_CSS = """
    CrossPanel { height: 5; border: round $accent; padding: 0; }
    """

    def __init__(self) -> None:
        super().__init__(id="cross")
        self.text = ""
        self.border_title = f"Panel cruzado {BASE_LABEL}/BTC"

    def on_mount(self) -> None:
        self.show(None, None)

    def show(self, nk: Quote | None, nx: Quote | None, note: str = "") -> None:
        text = render_cross(nk, nx, note)
        self.text = text.plain
        self.update(text)


def _age(ms: int) -> str:
    seconds = max(0, ms) // 1000
    return f"{seconds}s" if seconds < 120 else f"{seconds // 60}m"


def _pair(bal: Balance | None, decimals: int) -> str:
    if bal is None:
        bal = Balance("-", Decimal(0))
    if not bal.locked:  # lo en órdenes, solo si hay (D-67)
        return f"{bal.available:.{decimals}f}"
    return f"{bal.available:.{decimals}f} + {bal.locked:.{decimals}f}"


@dataclass(frozen=True, slots=True)
class AccountView:
    """Lo que el panel de balances necesita de un venue con keys."""

    balances: dict[str, Balance] | None
    balances_at: int | None
    error: str | None


def _ratio(pct: Decimal | None, target: InventoryTarget | None) -> Text:
    """% del valor en BTCB2 (RF-27): verde dentro de la banda, amarillo fuera."""
    if pct is None:
        return Text(f"{BASE_LABEL} — %")
    style = "" if target is None else "green" if target.in_band(pct) else "yellow"
    return Text(f"{BASE_LABEL} {pct:.1f} %", style=style)


def render_balances(
    views: dict[VenueId, AccountView | None],
    inv: Inventory,
    now: int,
    target: InventoryTarget | None = None,
) -> tuple[str, Text]:
    """(título, líneas). Una línea por venue: disponible + en órdenes de cada
    activo, su valor en BTC al mid y el % en BTCB2; en el título, el inventario
    neto total."""
    out = Text()
    for i, vid in enumerate(("nk", "nx")):
        if i:
            out.append("\n")
        out.append(f" {VENUE_NAME[vid]:<7}", style="bold")
        view = views.get(vid)
        if view is None:
            out.append("sin keys", style="dim")
            continue
        if view.balances is None:
            out.append("cargando…", style="dim")
        else:
            # 8 decimales siempre: el saldo puede tener más que el lote de órdenes
            out.append(f"BTC {_pair(view.balances.get('BTC'), 8)}")
            out.append(f" · {BASE_LABEL} {_pair(view.balances.get('BTCB2'), 8)}")
            item = inv.venues.get(vid)
            value = item.value if item else None
            out.append(" · valor ")
            out.append(f"{value:.8f} BTC" if value is not None else "—")
            out.append(" · ")
            out.append_text(_ratio(item.base_pct if item else None, target))
            if view.balances_at is not None:
                out.append(f" · hace {_age(now - view.balances_at)}", style="dim")
        if view.error:
            out.append(f" · ⚠ {view.error[:60]}", style="yellow")
    # corto: comparte la fila con las piernas de la doble (D-67)
    title = "Balances"
    if inv.btc is not None and inv.btcb2 is not None:
        title += f" · BTC {inv.btc:.8f} · {BASE_LABEL} {inv.btcb2:.8f}"
        title += f" · valor {inv.value:.8f} BTC" if inv.value is not None else " · valor —"
        if inv.base_pct is not None:
            title += f" · {BASE_LABEL} {inv.base_pct:.1f} %"
            if target is not None:
                title += f" (objetivo {target.target_pct} ±{target.band_pct})"
    return title, out


class BalancesPanel(Static):
    """Balances por venue e inventario neto (RF-05). Oculto sin cuenta."""

    DEFAULT_CSS = """
    BalancesPanel {
        height: 5; width: 1fr; border: round $success; padding: 0;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
    """

    def __init__(self, visible: bool, target: InventoryTarget | None = None) -> None:
        super().__init__(id="balances")
        self.text = ""
        self.display = visible
        self.target = target

    def show(self, views: dict[VenueId, AccountView | None], inv: Inventory, now: int) -> None:
        title, text = render_balances(views, inv, now, self.target)
        self.border_title = title
        self.text = text.plain
        self.update(text)


LEG_STATUS = {
    "pending": ("enviando", "yellow"),
    "open": ("abierta", "cyan"),
    "partial": ("parcial", "yellow"),
    "filled": ("llena", "green"),
    "cancelled": ("cancelada", "dim"),
    "rejected": ("rechazada", "bold red"),
    "unknown": ("INCIERTA", "bold magenta"),  # RNF-12: se está comprobando
}
LEG_SIDE = {"buy": "COMPRA", "sell": "VENTA"}
VENUE_NAME: dict[VenueId, str] = {"nk": "NonKYC", "nx": "Neoxa"}


def _leg_line(leg: Leg, spec: MarketSpec | None) -> Text:
    req = leg.req
    qd = _decimals(spec.lot_size) if spec else None
    pd = _decimals(spec.tick_size) if spec else None

    def num(value: Decimal, d: int | None) -> str:
        return f"{value:.{d}f}" if d is not None else f"{value:f}"

    label, color = LEG_STATUS[leg.status]
    out = Text(f" {LEG_SIDE[req.side]:<6} {VENUE_NAME[req.venue]:<6} ", style="bold")
    out.append(f"{num(req.qty, qd)} @ {num(req.price, pd)} · ")
    out.append(label, style=color)
    # lo lleno (lo pedido va delante de la @); el id de orden está en `o` (D-67)
    out.append(f" {num(leg.filled, qd)}")
    if leg.error:
        out.append(f" · {leg.error}", style="red")
    return out


def render_legs(
    state: DoubleState, reason: str | None, specs: dict[VenueId, MarketSpec | None]
) -> Text:
    out = Text()
    for leg in state.legs:
        out.append_text(_leg_line(leg, specs.get(leg.req.venue)))
        out.append("\n")
    if reason:
        out.append(f" ⚠ PIERNA COLGADA: {reason}", style="bold white on red")
    elif state.done:
        out.append(" terminada", style="dim")
    else:
        out.append(" en curso · sin cobertura automática", style="dim")
    return out


class LegsPanel(Static):
    """Piernas de la última orden doble (RF-07), a la derecha de los balances
    (D-67). Oculto hasta la primera."""

    DEFAULT_CSS = """
    LegsPanel {
        height: 5; width: 64; border: round $warning; padding: 0; display: none;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="legs")
        self.text = ""
        self.rendered = Text()

    def show(
        self, state: DoubleState, reason: str | None, specs: dict[VenueId, MarketSpec | None]
    ) -> None:
        self.rendered = render_legs(state, reason, specs)
        self.text = self.rendered.plain
        self.border_title = f"Orden doble {state.id}"
        self.display = True
        self.update(self.rendered)


class CommandLine(Input):
    """Línea de comandos (design.md §9). Deshabilitada si no se puede operar."""

    def __init__(self, enabled: bool, note: str = "", hint: str | None = None) -> None:
        if hint is not None:  # la TUI conectada al daemon (RF-31) trae el suyo
            pass
        elif enabled:
            hint = (
                "buy|sell <venue> <cant> <precio> · dbl <cant> <venue> <precio> <venue> <precio>"
                " · tf 1m…1d · clic en el book = precio · Esc suelta · ? ayuda"
            )
        else:
            hint = note or (
                "solo lectura: sin keys no hay órdenes · 1-5 timeframe · ? ayuda · q salir"
            )
        # sin select_on_focus: al teclear tras b/s/d o tras un clic en el book, el
        # primer carácter sustituiría todo el texto ya puesto
        super().__init__(placeholder=hint, id="cmd", disabled=not enabled, select_on_focus=False)

    def place_cursor(self, position: int) -> None:
        self.focus()
        self.cursor_position = position


class ResultsChart(PlotextPlot):
    """Resultado de las dobles cerradas en sats (RF-25): el acumulado como línea
    o cada doble como barra, verde si gana y roja si pierde."""

    DEFAULT_CSS = """
    ResultsChart { border: round $secondary; }
    """

    def __init__(self, kind: str, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.kind = kind  # "cum" | "bars"
        self.doubles: tuple[DoubleResult, ...] = ()
        self.border_title = "Acumulado (sats)" if kind == "cum" else "Por doble (sats)"

    def show(self, doubles: tuple[DoubleResult, ...]) -> None:
        self.doubles = tuple(d for d in doubles if d.result is not None)
        self.refresh()

    def render(self) -> RenderResult:
        self._draw()
        return super().render()

    def _draw(self) -> None:
        plt = self.plt
        plt.clear_data()
        if not self.doubles:
            plt.title("sin dobles cerradas en el rango")
            return
        xs = list(range(len(self.doubles)))
        values = [float(sats(d.result or 0)) for d in self.doubles]
        if self.kind == "cum":
            acc, cum = 0.0, []
            for v in values:
                acc += v
                cum.append(acc)
            plt.plot(xs, cum, color="cyan", marker="braille")
            plt.title(f"total {acc:+.0f} sats")
        else:
            for sign, color in ((1, "green"), (-1, "red")):
                pts = [(x, v) for x, v in zip(xs, values, strict=True) if (v >= 0) == (sign > 0)]
                if pts:
                    plt.bar([x for x, _ in pts], [v for _, v in pts], color=color, width=0.6)
            plt.title(f"{len(values)} dobles")
        span = self.doubles[-1].started - self.doubles[0].started
        fmt = "%H:%M" if span < 86_400_000 else "%m-%d %H:%M"
        every = max(1, len(xs) // 5)
        plt.xticks(
            xs[::every],
            [
                datetime.fromtimestamp(d.started / 1000, UTC).strftime(fmt)
                for d in self.doubles[::every]
            ],
        )


MAX_POINTS = 300  # plotext no distingue más puntos que columnas
VENUE_COLOR: dict[VenueId, str] = {"nk": "orange", "nx": "magenta"}


class ValueChart(PlotextPlot):
    """Valor de la cuenta en BTC contra la hora (RF-32): el total, o una línea
    por venue. Se corta en los huecos (ratos sin daemon), sin interpolar. En
    `relative` (el último tramo, D-69), el cambio en sats desde la primera
    muestra: en BTC las etiquetas del eje no distinguen unos sats."""

    DEFAULT_CSS = """
    ValueChart { border: round $secondary; }
    """

    def __init__(self, kind: str, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.kind = kind  # "total" | "venues"
        self.values: tuple[ValueSample, ...] = ()
        self.relative = False
        self._titles()

    def _titles(self) -> None:
        unit = "sats desde el inicio del tramo" if self.relative else "BTC"
        self.border_title = f"Total ({unit})" if self.kind == "total" else f"Por venue ({unit})"

    def show(self, values: tuple[ValueSample, ...], *, relative: bool = False) -> None:
        self.values = values
        self.relative = relative
        self._titles()
        self.refresh()

    def segments(self) -> list[tuple[ValueSample, ...]]:
        """Tramos sin huecos, cada uno reducido en proporción a su tamaño."""
        found = segments(self.values)
        n = len(self.values)
        return [thin(seg, max(2, MAX_POINTS * len(seg) // max(n, 1))) for seg in found]

    def render(self) -> RenderResult:
        self._draw()
        return super().render()

    def _y(self, value: Decimal, base: Decimal) -> float:
        return float(sats(value - base)) if self.relative else float(value)

    def _draw(self) -> None:
        plt = self.plt
        plt.clear_data()
        if not self.values:
            plt.title("aún sin muestras")
            return
        first_sample, last = self.values[0], self.values[-1]
        bases = {v.venue: v.value for v in first_sample.venues}
        labelled: set[str] = set()
        all_ys: list[float] = []
        for seg in self.segments():
            xs = [s.ts / 1000 for s in seg]
            if self.kind == "total":
                ys = [self._y(s.value, first_sample.value) for s in seg]
                all_ys += ys
                plt.plot(xs, ys, color="cyan", marker="braille")
                continue
            for i, vid in enumerate(v.venue for v in seg[0].venues):
                base = bases.get(vid, ZERO)
                ys = [self._y(s.venues[i].value, base) for s in seg if len(s.venues) > i]
                all_ys += ys
                name = VENUE_NAME.get(vid, vid)
                label = None if name in labelled else name
                labelled.add(name)
                plt.plot(
                    xs[: len(ys)], ys, color=VENUE_COLOR.get(vid, "white"),
                    marker="braille", label=label,
                )  # fmt: skip
        # eje ajustado a los datos; plano, plotext lo abriría un ±50 %
        lo, hi = min(all_ys), max(all_ys)
        pad = max((hi - lo) * 0.1, 1.0 if self.relative else 1e-8)
        plt.ylim(lo - pad, hi + pad)
        if self.kind == "total":
            if self.relative:
                plt.title(
                    f"{sats(last.value - first_sample.value):+.0f} sats · {last.value:.8f} BTC"
                )
            else:
                plt.title(f"{last.value:.8f} BTC")
        else:
            names = []
            for v in last.venues:
                name = VENUE_NAME.get(v.venue, v.venue)
                if self.relative:
                    names.append(f"{name} {sats(v.value - bases.get(v.venue, ZERO)):+.0f} sats")
                else:
                    names.append(f"{name} {v.value:.8f}")
            plt.title(" · ".join(names))
        first = first_sample.ts
        span = last.ts - first
        fmt = "%H:%M" if span < 86_400_000 else "%m-%d %H:%M"
        ticks = [first + span * k // 4 for k in range(5)] if span else [first]
        plt.xticks(
            [t / 1000 for t in ticks],
            [datetime.fromtimestamp(t / 1000, UTC).strftime(fmt) for t in ticks],
        )
