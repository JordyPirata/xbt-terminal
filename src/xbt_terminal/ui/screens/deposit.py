"""Direcciones de depósito por venue y activo.

Spec: RF-28 · Tareas: T-42, T-83
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ...core.models import BASE_LABEL, DepositAddress, DepositRow, asset_label
from ...services.remote import RemoteDeposits
from ..panels import VENUE_NAME
from ..qr import qr_lines


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
