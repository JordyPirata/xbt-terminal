"""Deposit addresses by venue and asset.

Spec: RF-28 · Tasks: T-42, T-83
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
    """BTC and BTCB2 deposit addresses per venue (RF-28). Asked of the venue on opening
    and with `r`; a row with an error never shows an address. On the right, the QR of
    the highlighted row to pay from a phone (D-52)."""

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
    BINDINGS = [  # noqa: RUF012  (Textual declares it so)
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
        self._copy = copy  # returns the system tool that copied, or None
        self.rows: list[DepositRow] | None = None  # None: not arrived yet
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
        for row in rows:
            table.add_row(*self._cells(row))
        self.rows = rows
        failed = sum(row.address is None for row in rows)
        self._status(f"{failed} sin dirección" if failed else "")
        self._show_qr(table.cursor_row)

    @staticmethod
    def _cells(row: DepositRow) -> tuple[str | Text, ...]:
        label = asset_label(row.asset)
        asset = f"{label} ({row.asset})" if label != row.asset else row.asset
        deposit_address = row.address
        if deposit_address is None:
            return (
                VENUE_NAME[row.venue],
                asset,
                "—",
                Text(f"⚠ {row.error}", style="yellow"),
                "—",
                "—",
            )
        network = (
            deposit_address.network
            if deposit_address.ticker.lower() == deposit_address.network.lower()
            else f"{deposit_address.network} · {deposit_address.ticker}"
        )
        address = deposit_address.address + (
            f"  (memo {deposit_address.memo})" if deposit_address.memo else ""
        )
        minimum = "—" if deposit_address.min_deposit is None else f"{deposit_address.min_deposit:f}"
        confs = "—" if deposit_address.confirmations is None else str(deposit_address.confirmations)
        return VENUE_NAME[row.venue], asset, network, address, minimum, confs

    def table_text(self) -> str:
        """Rows as plain text (tests and debugging)."""
        table = self.query_one(DataTable)
        return "\n".join(
            " | ".join(str(cell) for cell in table.get_row_at(i)) for i in range(table.row_count)
        )

    def status_text(self) -> str:
        return str(self.query_one("#deposit-status", Static).render())

    def qr_text(self) -> str:
        """The QR (or its notice) as plain text (tests)."""
        return self._qr

    def _status(self, text: str) -> None:
        self.query_one("#deposit-status", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_qr(event.cursor_row)

    def _show_qr(self, index: int) -> None:
        """Only the address, without `bitcoin:`: BTCB2 is not bitcoin (D-52)."""
        if not self.rows or not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        widget = self.query_one("#deposit-qr", Static)
        if row.address is None:
            self._qr = "sin QR: la fila no tiene dirección"
            widget.update(self._qr)
            return
        self._qr = "\n".join(qr_lines(row.address.address))
        caption = f"\n{asset_label(row.asset)} en {VENUE_NAME[row.venue]} ({row.address.ticker})"
        widget.update(Text(self._qr, style="#ffffff on #000000") + Text(caption))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_copy()

    def action_copy(self) -> None:
        if not self.rows:
            return
        row = self.rows[self.query_one(DataTable).cursor_row]
        where = f"{asset_label(row.asset)} en {VENUE_NAME[row.venue]}"
        if row.address is None:
            self._status(f"{where}: sin dirección, no se copia nada")
            return
        self.run_worker(self._copy_address(where, row.address), group="copy")

    async def _copy_address(self, where: str, deposit_address: DepositAddress) -> None:
        tool = await self._copy(deposit_address.address)
        if tool is not None:
            self._status(
                f"copiada con {tool} la dirección de {where} "
                f"({deposit_address.ticker}): {deposit_address.address}"
            )
        else:
            self._status(
                f"enviada solo a la terminal (OSC 52), que puede ignorarla; si no se pega, "
                f"selecciónala con Shift + arrastrar · {where} "
                f"({deposit_address.ticker}): {deposit_address.address}"
            )

    def action_close(self) -> None:
        self.dismiss(None)
