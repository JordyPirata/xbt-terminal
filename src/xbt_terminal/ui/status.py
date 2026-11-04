"""Status bar: connection and latency per venue and channel, and trading mode.

A fixed line at the very top. The market channel comes from the `ConnState`s MarketHub
keeps (D-11: the supervisor re-emits the state every 5 s with the latency). After
`STALE_AFTER_MS` without news it warns: something hung without being marked down;
only for "up", because reconnecting has no heartbeats. The private channel comes from
AccountService (T-17): `ws` (NonKYC with WS reports) or `rest` (polling), with its
state; without keys, "sin keys", and in dry-run "simulado", because orders and
balances are simulated (D-13). `live` mode has a permanent red banner (design.md §8).
Before the mode, orders with an uncertain send still to reconcile (RNF-12).

With the bot active (phase 2, RF-23), its state takes the mode banner's text
(`LIVE · BOT armado`): the bar has no room for another block in the worst case (D-37).
Stopped (`halted`) is red in any mode; off is not shown.

Spec: RF-13, RF-14, RNF-12 · design.md §8, §9 · Tasks: T-15, T-17, T-25
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

from ..core.models import ConnState, VenueId

STALE_AFTER_MS = 15_000  # 3 heartbeats of 5 s without news
NAMES: dict[VenueId, str] = {"nk": "NonKYC", "nx": "Neoxa"}
MODES = {
    "read-only": ("SOLO LECTURA", "bold black on grey70"),
    "dry-run": ("DRY-RUN (simulado)", "bold black on yellow"),
    "live": ("LIVE · ÓRDENES REALES", "bold white on red"),
}
BOT = {
    "armed": "BOT armado",
    "firing": "BOT dispara",
    "cooldown": "BOT enfría",
    "halted": "BOT ✖ PARADO",
}
STATUS = {
    "up": ("●", "conectado", "green"),
    "reconnecting": ("◌", "reconectando", "yellow"),
    "down": ("✖", "caído", "bold red"),
}


@dataclass(frozen=True, slots=True)
class VenueLink:
    market: ConnState | None
    seen_at: int | None  # when the last state arrived (local time, ms)
    can_trade: bool
    private: ConnState | None = None


def _age(ms: int) -> str:
    seconds = max(0, ms) // 1000
    return f"{seconds}s" if seconds < 120 else f"{seconds // 60}m"


def _private(link: VenueLink, mode: str, now: int) -> Text:
    if not link.can_trade:
        return Text("sin keys", style="dim")
    state = link.private
    if state is None:
        return Text("simulado" if mode == "dry-run" else "—", style="dim")
    icon, label, color = STATUS[state.status]
    out = Text(icon, style=color)
    if state.status == "up":
        out.append(f" {state.source}")
    else:
        out.append(f" {label} {_age(now - state.since)}")
    return out


def _venue(venue: VenueId, link: VenueLink, now: int, mode: str) -> Text:
    """Market channel: `ws` or `respaldo` (REST polling, RF-01) + state; the worst case
    fits 160 columns with both venues and the live banner."""
    out = Text(f" {NAMES[venue]} ", style="bold")
    state = link.market
    if state is None:
        out.append("conectando…", style="dim")
    else:
        icon, label, color = STATUS[state.status]
        out.append("ws " if state.source == "ws" else "respaldo ")
        if state.status == "up":
            latency = "—" if state.latency_ms is None else f"{state.latency_ms:.0f}"
            out.append(icon, style=color)
            out.append(f" {latency} ms")
            # "up" without heartbeats: looks alive but is frozen. Reconnecting has no heartbeats
            # (D-11) and the age is already shown.
            if link.seen_at is not None and now - link.seen_at > STALE_AFTER_MS:
                out.append(f" ⚠ sin noticias {_age(now - link.seen_at)}", style="bold yellow")
        else:
            out.append(f"{icon} {label}", style=color)
            out.append(f" {_age(now - state.since)}")
    out.append(" · privado ")
    out.append_text(_private(link, mode, now))
    return out


def render_status(
    links: Mapping[VenueId, VenueLink],
    mode: str,
    now: int,
    *,
    uncertain: int = 0,
    faults: int = 0,
    bot: str | None = None,
    daemon: bool | None = None,
) -> Text:
    """`daemon`: None in tests without a daemon; in the client (RF-31), whether there is a
    connection with the daemon."""
    out = Text()
    for venue in ("nk", "nx"):
        out.append_text(_venue(venue, links[venue], now, mode))
        out.append(" │")
    if faults:  # internal faults of the session (RNF-14): the detail is in the journal
        out.append(" ")
        label = "fallo" if faults == 1 else "fallos"
        out.append(f" ✖ {faults} {label} ", style="bold white on red")
    if uncertain:
        label = "incierta" if uncertain == 1 else "inciertas"
        out.append(" ")
        out.append(f" ⚠ {uncertain} {label} ", style="bold white on magenta")
    if daemon is not None:
        out.append(" ")
        if daemon:
            out.append(" ⇄ daemon ", style="bold black on cyan")
        else:
            out.append(" ✖ sin daemon ", style="bold white on red")
    label, style = MODES[mode]
    if bot in BOT:
        label = f"{label.split(' ')[0]} · {BOT[bot]}"  # "LIVE · BOT armado"
        if bot == "halted":
            style = "bold white on red"
    out.append(" ")
    out.append(f" {label} ", style=style)
    return out


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar { dock: top; height: 1; width: 100%; }
    """

    def __init__(self) -> None:
        super().__init__(id="status")
        self.text = ""

    def show(
        self,
        links: Mapping[VenueId, VenueLink],
        mode: str,
        now: int,
        *,
        uncertain: int = 0,
        faults: int = 0,
        bot: str | None = None,
        daemon: bool | None = None,
    ) -> None:
        text = render_status(
            links, mode, now, uncertain=uncertain, faults=faults, bot=bot, daemon=daemon
        )
        self.text = text.plain
        self.update(text)
