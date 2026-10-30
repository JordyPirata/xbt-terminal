"""Órdenes abiertas, con cancelación por fila.

Spec: RF-08 · Tareas: T-19, T-83
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ...core.commands import CancelAll, CancelOrder, CancelVenue
from ...core.models import Order, VenueId
from ..panels import VENUE_NAME

CancelCommand = CancelOrder | CancelVenue | CancelAll


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
