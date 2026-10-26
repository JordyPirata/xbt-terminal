"""Barra de estado: conexión y latencia por venue y canal, y modo de operación.

Una línea fija arriba del todo. El canal de mercado sale de los `ConnState` que
guarda MarketHub (D-11: el supervisor reemite el estado cada 5 s con la
latencia). Si pasan `STALE_AFTER_MS` sin noticias, se avisa: algo se ha colgado
sin llegar a marcarse como caído; solo aplica a "up", porque reconectando no
hay latidos. El canal privado sale de AccountService (T-17): `ws` (NonKYC con
reportes por WS) o `rest` (polling), con su estado; sin keys, "sin keys", y en
dry-run "simulado", porque órdenes y saldos son simulados (D-13). El modo `live`
lleva un banner rojo permanente (design.md §8). Antes del modo, las órdenes con
envío incierto que quedan por reconciliar (RNF-12).

Con el bot activo (fase 2, RF-23), su estado ocupa el texto del banner de modo
(`LIVE · BOT armado`): la barra no tiene sitio para otro bloque en el peor caso
(D-37). Parado (`halted`) va en rojo en cualquier modo; apagado no se ve.

Spec: RF-13, RF-14, RNF-12 · design.md §8, §9 · Tareas: T-15, T-17, T-25
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

from ..core.models import ConnState, VenueId

STALE_AFTER_MS = 15_000  # 3 latidos de 5 s sin noticias
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
    seen_at: int | None  # cuándo llegó el último estado (hora local, ms)
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
    """Canal de mercado: `ws` o `respaldo` (polling REST, RF-01) + estado; el
    peor caso cabe en 160 columnas con los dos venues y el banner live."""
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
            # "up" sin latidos: parece viva pero está congelada. Reconectando no
            # hay latidos (D-11) y la antigüedad ya se muestra.
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
    """`daemon`: None en los tests sin daemon; en el cliente (RF-31), si hay
    conexión con el daemon."""
    out = Text()
    for venue in ("nk", "nx"):
        out.append_text(_venue(venue, links[venue], now, mode))
        out.append(" │")
    if faults:  # fallos internos de la sesión (RNF-14): el detalle está en el journal
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
