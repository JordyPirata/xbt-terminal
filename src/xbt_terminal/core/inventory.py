"""Net inventory (RF-05, design.md §6.5).

Per venue: total BTC and BTCB2 (available + locked) and their BTC value at that
venue's mid. In total: the sum of both. If a venue has no mid, its value is None
and so is the total, because a partial total would mislead. Only BTC and BTCB2
count (RNF-10).

Inventory ratio (RF-26, D-46): the % of a venue's value in BTCB2, and a target
band around a %; a double "worsens" a venue if it leaves it outside the band and
further from the target than before.

Spec: RF-05, RF-26, RF-27, RNF-10 · design.md §6.5, §6.9 · Tasks: T-19, T-41
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from .models import ZERO, Balance, VenueId

BASE, QUOTE = "BTCB2", "BTC"
HUNDRED = Decimal(100)


def base_pct(btc: Decimal, btcb2: Decimal, price: Decimal) -> Decimal | None:
    """% of the value in BTCB2 at `price`; None if there is nothing to value."""
    value = btc + btcb2 * price
    return btcb2 * price / value * HUNDRED if value > 0 else None


def _pct_of(btc: Decimal, value: Decimal | None) -> Decimal | None:
    return (value - btc) / value * HUNDRED if value is not None and value > 0 else None


@dataclass(frozen=True, slots=True)
class InventoryTarget:
    """`[inventory]` section: target BTCB2 % and band on each side (RF-26)."""

    target_pct: Decimal
    band_pct: Decimal

    @property
    def low(self) -> Decimal:
        return max(ZERO, self.target_pct - self.band_pct)

    @property
    def high(self) -> Decimal:
        return min(HUNDRED, self.target_pct + self.band_pct)

    def in_band(self, percent: Decimal) -> bool:
        return self.low <= percent <= self.high

    def worsens(self, before: Decimal, after: Decimal) -> bool:
        """Outside the band and further from the target than before."""
        return not self.in_band(after) and abs(after - self.target_pct) > abs(
            before - self.target_pct
        )


@dataclass(frozen=True, slots=True)
class VenueInventory:
    btc: Decimal
    btcb2: Decimal
    value: Decimal | None  # in BTC at the venue mid

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
    for venue_id, bal in balances.items():
        if bal is None:
            venues[venue_id] = None
            continue
        btc, btcb2 = _total(bal, QUOTE), _total(bal, BASE)
        mid = mids.get(venue_id)
        venues[venue_id] = VenueInventory(
            btc, btcb2, btc + btcb2 * mid if mid is not None else None
        )
    known = [v for v in venues.values() if v is not None]
    complete = len(known) == len(venues)
    values = [v.value for v in known]
    return Inventory(
        venues=venues,
        btc=sum((v.btc for v in known), ZERO) if complete else None,
        btcb2=sum((v.btcb2 for v in known), ZERO) if complete else None,
        value=sum(values, ZERO) if complete and None not in values else None,  # type: ignore[arg-type]
    )
