"""The watched self-custody addresses (RF-37): the one place they are shown (RNF-25).

Spec: RF-37, RNF-25 · design.md §6.5 · D-85 · Task: T-109
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...core.models import BASE_LABEL
from ...services.wire import Watched


def watch_text(watched: Watched) -> str:
    sections = [("BTC", watched.btc), (BASE_LABEL, watched.xbt)]
    lines = ["DIRECCIONES VIGILADAS", ""]
    for label, addresses in sections:
        lines.append(f"{label} ({len(addresses)})")
        lines.extend(f"  {address}" for address in addresses)
        if not addresses:
            lines.append("  ninguna")
        lines.append("")
    lines.append("watch add btc|xbt <dirección> · watch rm btc|xbt <dirección> · [Esc] cerrar")
    return "\n".join(lines)


class WatchScreen(ModalScreen[None]):
    """List of the watched addresses. No markup: it is text the user typed."""

    DEFAULT_CSS = """
    WatchScreen { align: center middle; }
    WatchScreen > VerticalScroll {
        width: 90; height: auto; max-height: 40; border: thick $accent;
        background: $panel; padding: 0 1;
    }
    """
    BINDINGS = [  # noqa: RUF012  (Textual declares it so)
        Binding("escape", "close", "cerrar"),
        Binding("q", "close", show=False),
    ]

    def __init__(self, watched: Watched) -> None:
        super().__init__()
        self.text = watch_text(watched)

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.text, markup=False)

    def action_close(self) -> None:
        self.dismiss(None)
