"""Inventario neto (RF-05, design.md §6.5).

Por venue: BTC y BTCB2 totales (disponible + bloqueado) y su valor en BTC al mid
de ese venue. En total: la suma de los dos. Si a un venue le falta el mid, su
valor es None y el total también, porque un total a medias engañaría. Solo
cuentan BTC y BTCB2 (RNF-10).

Ratio de inventario (RF-26, D-46): el % del valor de un venue que está en
BTCB2, y una banda objetivo alrededor de un %; una doble "empeora" un venue si
lo deja fuera de la banda y más lejos del objetivo que antes.

Spec: RF-05, RF-26, RF-27, RNF-10 · design.md §6.5, §6.9 · Tareas: T-19, T-41
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from .models import ZERO, Balance, VenueId

BASE, QUOTE = "BTCB2", "BTC"
HUNDRED = Decimal(100)


def base_pct(btc: Decimal, btcb2: Decimal, price: Decimal) -> Decimal | None:
    """% del valor en BTCB2 a `price`; None si no hay nada que valorar."""
    value = btc + btcb2 * price
    return btcb2 * price / value * HUNDRED if value > 0 else None


def _pct_of(btc: Decimal, value: Decimal | None) -> Decimal | None:
    return (value - btc) / value * HUNDRED if value is not None and value > 0 else None


@dataclass(frozen=True, slots=True)
class InventoryTarget:
    """Sección `[inventory]`: % objetivo de BTCB2 y banda a cada lado (RF-26)."""

    target_pct: Decimal
    band_pct: Decimal

    @property
    def low(self) -> Decimal:
        return max(ZERO, self.target_pct - self.band_pct)

    @property
    def high(self) -> Decimal:
        return min(HUNDRED, self.target_pct + self.band_pct)

    def in_band(self, pct: Decimal) -> bool:
        return self.low <= pct <= self.high

    def worsens(self, before: Decimal, after: Decimal) -> bool:
        """Fuera de la banda y más lejos del objetivo que antes."""
        return not self.in_band(after) and abs(after - self.target_pct) > abs(
            before - self.target_pct
        )


@dataclass(frozen=True, slots=True)
class VenueInventory:
    btc: Decimal
    btcb2: Decimal
    value: Decimal | None  # en BTC al mid del venue

    @property
    def base_pct(self) -> Decimal | None:
        return _pct_of(self.btc, self.value)


@dataclass(frozen=True, slots=True)
class Inventory:
    venues: dict[VenueId, VenueInventory | None]
    btc: Decimal | None
    btcb2: Decimal | None
    value: Decimal | None

    @property
    def base_pct(self) -> Decimal | None:
        return _pct_of(self.btc, self.value) if self.btc is not None else None


def _total(balances: Mapping[str, Balance], asset: str) -> Decimal:
    bal = balances.get(asset)
    return bal.total if bal else ZERO


def inventory(
    balances: Mapping[VenueId, Mapping[str, Balance] | None],
    mids: Mapping[VenueId, Decimal | None],
) -> Inventory:
    venues: dict[VenueId, VenueInventory | None] = {}
    for vid, bal in balances.items():
        if bal is None:
            venues[vid] = None
            continue
        btc, btcb2 = _total(bal, QUOTE), _total(bal, BASE)
        mid = mids.get(vid)
        venues[vid] = VenueInventory(btc, btcb2, btc + btcb2 * mid if mid is not None else None)
    known = [v for v in venues.values() if v is not None]
    complete = len(known) == len(venues)
    values = [v.value for v in known]
    return Inventory(
        venues=venues,
        btc=sum((v.btc for v in known), ZERO) if complete else None,
        btcb2=sum((v.btcb2 for v in known), ZERO) if complete else None,
        value=sum(values, ZERO) if complete and None not in values else None,  # type: ignore[arg-type]
    )
