"""Rebalance plan between venues.

Spec: RF-29 · Tasks: T-45, T-83
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ...core.bot import BotLimits
from ...core.inventory import InventoryTarget
from ...core.models import BASE_LABEL, VenueId, asset_label
from ...core.rebalance import Holding, Plan, Transfer, break_even, plan
from ...services.remote import RemoteRebalance
from ..format import fmt_sats_grouped, fmt_share
from ..panels import VENUE_NAME


class RebalanceScreen(ModalScreen[None]):
    """Rebalance plan (RF-29): two alternatives of a transfer equalling the venues' BTCB2
    %, with the fees asked on opening and with `r`. It only computes (D-53)."""

    DEFAULT_CSS = """
    RebalanceScreen { align: center middle; }
    RebalanceScreen > Vertical {
        width: 150; height: 26; max-height: 100%;
        border: thick $accent; background: $panel; padding: 0 1;
    }
    RebalanceScreen DataTable { height: 5; }
    RebalanceScreen Static { height: auto; }
    """
    BINDINGS = [  # noqa: RUF012  (Textual declares it so)
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
        self.plan: Plan | None = None  # None: no plan yet

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
        held = self._holdings()  # current balances, not those from before asking
        result = held if isinstance(held, str) else plan(held, fees)
        if isinstance(result, str):
            self._fail(result)
            return
        self._show("rebalance-state", self._state(result))
        table = self.query_one(DataTable)
        table.clear()
        for transfer in result.transfers:
            table.add_row(*self._cells(transfer))
        self._show("rebalance-notes", self._notes(result, held))
        self.plan = result

    def _fail(self, why: str) -> None:
        self.query_one(DataTable).clear()
        self._show("rebalance-state", "")
        self._show("rebalance-notes", f"⚠ no hay plan: {why}")

    @staticmethod
    def _state(rebalance_plan: Plan) -> str:
        venues = " · ".join(
            f"{VENUE_NAME[venue]} {fmt_share(percent)}"
            for venue, percent in rebalance_plan.before.items()
        )
        return (
            f"{BASE_LABEL} por venue ahora: {venues} · total {fmt_share(rebalance_plan.total_pct)}"
        )

    @staticmethod
    def _route(transfer: Transfer) -> str:
        return (
            f"{asset_label(transfer.asset)} {VENUE_NAME[transfer.source]} "
            f"→ {VENUE_NAME[transfer.dest]}"
        )

    @classmethod
    def _cells(cls, transfer: Transfer) -> tuple[str | Text, ...]:
        fee = "—" if transfer.fee is None else f"{transfer.fee:f}"
        after = " · ".join(
            f"{VENUE_NAME[venue]} {fmt_share(percent)}" for venue, percent in transfer.after.items()
        )
        state = (
            Text("viable", style="green")
            if transfer.viable
            else Text(f"⚠ {transfer.problem}", "yellow")
        )
        return (
            cls._route(transfer),
            f"{transfer.amount:f}",
            fee,
            f"{transfer.debited:f}",
            f"{transfer.received:f}",
            fmt_sats_grouped(transfer.cost_btc),
            after,
            state,
        )

    def _notes(self, rebalance_plan: Plan, held: Mapping[VenueId, Holding]) -> str:
        best = rebalance_plan.best
        if best is None:
            lines = ["ninguna alternativa es viable ahora"]
        else:
            lines = [
                f"Recomendada: {self._route(best)}, pedir {best.amount:f} "
                f"{asset_label(best.asset)} en el retiro (salen {best.debited:f}, llegan "
                f"{best.received:f}); comisión {fmt_sats_grouped(best.cost_btc)}"
            ]
            if self._limits is not None and best.cost_btc is not None:
                lim = self._limits
                price = held[best.source].price
                units = break_even(best.cost_btc, lim.max_qty, price, lim.min_net_pct)
                if units is not None:
                    lines.append(
                        f"la pagan ≈ {units} dobles del bot de {lim.max_qty:f} {BASE_LABEL} "
                        f"a la neta mínima ({lim.min_net_pct} %)"
                    )
        target = self._target
        if (
            target is not None
            and rebalance_plan.total_pct is not None
            and not target.in_band(rebalance_plan.total_pct)
        ):
            lines.append(
                f"⚠ el total ({fmt_share(rebalance_plan.total_pct)}) está fuera de la banda "
                f"{target.low}-{target.high} %: "
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
        """Everything as plain text (tests and debugging)."""
        table = self.query_one(DataTable)
        rows = [
            " | ".join(str(cell) for cell in table.get_row_at(i)) for i in range(table.row_count)
        ]
        parts = [
            str(self.query_one(f"#rebalance-{section}", Static).render())
            for section in ("head", "state")
        ]
        parts += rows
        parts.append(str(self.query_one("#rebalance-notes", Static).render()))
        return "\n".join(parts)

    def action_close(self) -> None:
        self.dismiss(None)
