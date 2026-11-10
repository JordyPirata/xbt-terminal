"""Double results and account value over time.

Spec: RF-25, RF-32, CA-17 · Tasks: T-40, T-69, T-70, T-71, T-83, T-94
"""

from __future__ import annotations

import asyncio
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
RESULTS_EVERY_S = 60.0  # while open, the report is asked again (RF-32, D-69)


class ResultsScreen(ModalScreen[None]):
    """Realized result of double orders (RF-25): charts, summary and table. Recomputed on
    opening, on changing range, with each fill and every `RESULTS_EVERY_S` while open.
    On another tab, the account value over time (RF-32)."""

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
    BINDINGS = [  # noqa: RUF012  (Textual declares it so)
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
        self.zoom = False  # account value: only the last stretch (D-69)

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
        self._draw_title()  # with the mouse too

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "inicio UTC", "doble", "origen", "ruta", "casada", "compra", "venta",
            "fees sats", "esperada", "real", "resultado sats", "descuadre", "estado",
        )  # fmt: skip
        self.reload()
        self.refresh_report()
        self.query_one(DataTable).focus()
        # the daemon keeps writing samples and fills: open does not freeze (D-69)
        self.set_interval(RESULTS_EVERY_S, self.refresh_report)

    def refresh_report(self) -> None:
        """Ask for what is missing (token price, D-65; with a daemon, the report, D-66) in the
        background and redraw."""

        async def work() -> None:
            if error := await self._source.refresh(self.range):
                self._on_error(f"resultados: {error}")
            rng = self.range
            # every minute and with each fill: the journal is read off the UI thread (T-94)
            try:
                report = await asyncio.to_thread(self._source.load, rng)
            except OSError as exception:
                self._on_error(f"resultados: no se pudo leer el journal ({exception})")
                return
            if rng == self.range:  # a range change while reading already drew its own
                self._show(report)

        self.run_worker(work(), group="results-price", exclusive=True)

    def reload(self) -> None:
        try:
            report = self._source.load(self.range)
        except OSError as error:
            self._on_error(f"resultados: no se pudo leer el journal ({error})")
            return
        self._show(report)

    def _show(self, report: Report) -> None:
        if report == self.report:
            return  # no changes: no redraw and no selection move
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
        selected = None  # the selected row stays on its double on redraw
        if table.row_count:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear()
        priced = {token for token, _ in self.report.summary.prices}
        for double in reversed(self.report.doubles):  # most recent on top
            table.add_row(*self._cells(double, priced), key=double.id)
        if selected is not None and selected in table.rows:
            table.move_cursor(row=table.get_row_index(selected), animate=False)

    @staticmethod
    def _cells(double: DoubleResult, priced: Collection[str] = ()) -> tuple[str, ...]:
        """`priced`: tokens whose fee is already in sats (D-65)."""
        started = datetime.fromtimestamp(double.started / 1000, UTC).strftime("%m-%d %H:%M:%S")
        avg = lambda value: "—" if value is None else f"{value:.8f}"  # noqa: E731
        fees = fmt_sats(double.fees + double.token_fees, signed=False)
        valued = [
            f"{quantity:f} {asset}" for asset, quantity in double.other_fees if asset in priced
        ]
        loose = [
            f"{quantity:f} {asset}" for asset, quantity in double.other_fees if asset not in priced
        ]
        if valued:  # already in sats (D-65): the quantity, to know where they come from
            fees += f" ({', '.join(valued)})"
        if loose:
            fees += " + " + ", ".join(loose)
        return (
            started,
            double.id,
            double.origin,
            f"{double.buy_venue or '?'}→{double.sell_venue or '?'}",
            f"{double.matched:f}", avg(double.buy_avg), avg(double.sell_avg), fees,
            fmt_pct(double.expected_pct), fmt_pct(double.result_pct), fmt_sats(double.result),
            "—" if double.mismatch == 0 else f"{double.mismatch:+f}", STATUS_NAMES[double.status],
        )  # fmt: skip

    def summary_text(self) -> str:
        if self.report is None:
            return ""
        summary = self.report.summary
        parts = [f"{summary.closed} dobles cerradas"]
        if summary.closed:
            parts += [
                f"total {summary.total:+.8f} BTC · {fmt_sats(summary.total)} sats",
                f"media {fmt_sats(summary.mean)} · mejor {fmt_sats(summary.best)} "
                f"· peor {fmt_sats(summary.worst)} sats",
            ]
        if summary.prices:  # RF-25, D-65: token fees subtracted at today's price
            used = ", ".join(f"{asset} a {price:.8f} BTC" for asset, price in summary.prices)
            parts.append(f"fees en token restadas con {used} (precio de ahora)")
        if summary.other_fees:  # no price: not valued in BTC
            other = ", ".join(f"{quantity:f} {asset}" for asset, quantity in summary.other_fees)
            parts.append(f"+ {other} en fees, sin descontar del total")
        extra = [
            (summary.hanging, "colgadas (no suman)"),
            (summary.no_fills, "sin fills"),
            (summary.unassigned, "fills sin asignar a una orden de la terminal"),
            (summary.bad_rows, "líneas del journal ilegibles"),
        ]
        parts += [f"{number} {what}" for number, what in extra if number]
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
        """Series summary (RF-32) and what it is not: deposits and withdrawals unadjusted,
        gaps without daemon."""
        values = self.report.values if self.report is not None else ()
        summary = summarize(values)
        if summary is None:
            return (
                "aún sin muestras en el rango: el daemon apunta una cada `value_every_s` "
                "(5 min por defecto) si tiene saldos y mid de los dos venues"
            )
        fmt = "%m-%d %H:%M"
        since = datetime.fromtimestamp(summary.since / 1000, UTC).strftime(fmt)
        until = datetime.fromtimestamp(summary.until / 1000, UTC).strftime(fmt)
        percent = f" ({summary.change_pct:+.2f} %)" if summary.change_pct is not None else ""
        venues = " · ".join(
            f"{VENUE_NAME.get(venue.venue, venue.venue)} {venue.value:.8f}"
            for venue in values[-1].venues
        )
        return "\n".join([
            f"ahora {summary.last:.8f} BTC ({venues}) · cambio {summary.change:+.8f} BTC{percent} "
            f"desde {summary.first:.8f}",
            f"máx {summary.high:.8f} · mín {summary.low:.8f} · "
            f"{summary.count} muestras de {since} a {until} UTC",
            *self._stretch_text(),
            "Los depósitos y retiros salen como saltos: es el valor, no es PnL. "
            "Los huecos son ratos sin daemon. Valor = BTC + BTCB2 × mid de cada venue.",
        ])  # fmt: skip

    def _stretch_text(self) -> list[str]:
        """With `z`: when the stretch is from and how much it changed in sats."""
        if not self.zoom:
            return []
        summary = summarize(self._shown_values())
        if summary is None:
            return []
        since = datetime.fromtimestamp(summary.since / 1000, UTC).strftime("%m-%d %H:%M")
        return [
            f"último tramo desde {since} UTC (tras el último salto > 5 %): "
            f"cambio {fmt_sats(summary.change)} sats en {summary.count} muestras"
        ]

    def row_count(self) -> int:
        return self.query_one(DataTable).row_count

    def action_range(self, rng: Range) -> None:
        self.range = rng
        self.reload()
        self.refresh_report()

    def action_close(self) -> None:
        self.dismiss(None)
