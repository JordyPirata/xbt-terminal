"""Notices and faults: what the engine reports and the screen shows. Pure data, so
the UI does not import the services (D-66).

Spec: RF-31, RNF-14 · design.md §6.7, §6.13 · Tasks: T-27, T-50, T-65 · D-28, D-66
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import VenueId


@dataclass(frozen=True, slots=True)
class Notice:
    text: str
    error: bool = False


WHERE: dict[str, str] = {
    "market": "mercado",
    "private": "canal privado",
    "balances": "saldos",
    "journal": "journal",
    "reconcile": "reconciliación",
    "ui": "terminal",
    "engine": "motor",
    "control": "socket de control",
    "value": "valor de la cuenta",
}
VENUE: dict[str, str] = {"nk": "NonKYC", "nx": "Neoxa"}


@dataclass(frozen=True, slots=True)
class Fault:
    where: str
    venue: VenueId | None
    error: str  # "Type: message", already redacted
    ts: int
    note: str | None = None

    @property
    def message(self) -> str:
        if self.note:  # the journal could not be written: there is no detail to look at
            return f"✖ {self.note}: {self.error}"
        place = WHERE.get(self.where, self.where)
        if self.venue is not None:
            place = f"{place} de {VENUE[self.venue]}"
        return f"✖ fallo en {place}: {self.error} (detalle en el journal)"
