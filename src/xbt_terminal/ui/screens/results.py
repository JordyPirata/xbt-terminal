"""Resultados de las dobles y valor de la cuenta en el tiempo.

Spec: RF-25, RF-32, CA-17 · Tareas: T-40, T-69, T-70, T-71, T-83
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static, TabbedContent, TabPane

from ...core.results import DoubleResult, Range, Report
from ...core.valuation import ValueSample, last_stretch, summarize
from ...services.remote import RemoteResults
from ..format import fmt_pct, fmt_sats
from ..panels import VENUE_NAME, ResultsChart, ValueChart

RANGE_NAMES: dict[Range, str] = {"dia": "hoy (UTC)", "7d": "7 días", "todo": "todo"}
STATUS_NAMES = {"closed": "cerrada", "hanging": "COLGADA", "no_fills": "sin fills"}
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
        fees = fmt_sats(d.fees + d.token_fees, signed=False)
        valued = [f"{q:f} {a}" for a, q in d.other_fees if a in priced]
        loose = [f"{q:f} {a}" for a, q in d.other_fees if a not in priced]
        if valued:  # ya en sats (D-65): la cantidad, para saber de dónde salen
            fees += f" ({', '.join(valued)})"
        if loose:
            fees += " + " + ", ".join(loose)
        return (
            started, d.id, d.origin, f"{d.buy_venue or '?'}→{d.sell_venue or '?'}",
            f"{d.matched:f}", avg(d.buy_avg), avg(d.sell_avg), fees,
            fmt_pct(d.expected_pct), fmt_pct(d.result_pct), fmt_sats(d.result),
            "—" if d.mismatch == 0 else f"{d.mismatch:+f}", STATUS_NAMES[d.status],
        )  # fmt: skip

    def summary_text(self) -> str:
        if self.report is None:
            return ""
        s = self.report.summary
        parts = [f"{s.closed} dobles cerradas"]
        if s.closed:
            parts += [
                f"total {s.total:+.8f} BTC · {fmt_sats(s.total)} sats",
                f"media {fmt_sats(s.mean)} · mejor {fmt_sats(s.best)} "
                f"· peor {fmt_sats(s.worst)} sats",
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
            f"cambio {fmt_sats(s.change)} sats en {s.count} muestras"
        ]

    def row_count(self) -> int:
        return self.query_one(DataTable).row_count

    def action_range(self, rng: Range) -> None:
        self.range = rng
        self.reload()
        self.refresh_report()

    def action_close(self) -> None:
        self.dismiss(None)
