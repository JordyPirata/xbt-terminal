"""Confirmación de una orden, una cancelación, salir o armar el bot.

Spec: RF-06, RF-07, RF-08, RF-18 · Tareas: T-83
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

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
