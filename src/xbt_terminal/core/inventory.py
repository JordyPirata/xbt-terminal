"""Net inventory (RF-05, design.md §6.5).

Per venue: total BTC and BTCB2 (available + locked) and their BTC value at that
venue's mid. In total: the sum of both. If a venue has no mid, its value is None
and so is the total, because a partial total would mislead. Only BTC and BTCB2
count (RNF-10).

Self-custody (RF-39, D-84): the wallets' on-chain balance joins the total, never a
venue, with its BTCB2 valued at the mean of the venue mids. If wallets are configured
and their balance is missing, there is no total. Callers that do not pass it (the
RF-10 order cap, the bot) see the venues only, as before.

Inventory ratio (RF-26, D-46): the % of a venue's value in BTCB2, and a target
band around a %; a double "worsens" a venue if it leaves it outside the band and
further from the target than before.

Spec: RF-05, RF-26, RF-27, RF-39, RNF-10 · design.md §6.5, §6.9 · Tasks: T-19, T-41, T-104
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
class CustodyBalance:
    """On-chain balance of the self-custody wallets, from one complete scan (RF-38)."""

    btc: Decimal
    btcb2: Decimal
    at: int  # epoch ms of the scan


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
    custody: VenueInventory | None = None  # self-custody wallets, part of the totals
    custody_mid: Decimal | None = None  # price its BTCB2 is valued at

    @property
    def base_pct(self) -> Decimal | None:
        return _pct_of(self.btc, self.value) if self.btc is not None else None


def _total(balances: Mapping[str, Balance], asset: str) -> Decimal:
    bal = balances.get(asset)
    return bal.total if bal else ZERO


def inventory(
    balances: Mapping[VenueId, Mapping[str, Balance] | None],
    mids: Mapping[VenueId, Decimal | None],
    custody: CustodyBalance | None = None,
    *,
    has_custody: bool = False,
) -> Inventory:
    """`has_custody` with no `custody`: wallets are configured but their balance is
    missing or too old, so the totals are None rather than partial (RF-39)."""
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
    known = [venue_inventory for venue_inventory in venues.values() if venue_inventory is not None]
    complete = len(known) == len(venues)
    values = [venue_inventory.value for venue_inventory in known]
    wallet: VenueInventory | None = None
    wallet_mid: Decimal | None = None
    if custody is not None:
        venue_mids = [mids.get(venue_id) for venue_id in venues]
        if venue_mids and None not in venue_mids:
            wallet_mid = sum(venue_mids, ZERO) / len(venue_mids)  # type: ignore[arg-type]
        wallet = VenueInventory(
            custody.btc,
            custody.btcb2,
            custody.btc + custody.btcb2 * wallet_mid if wallet_mid is not None else None,
        )
        known = [*known, wallet]
        values = [*values, wallet.value]
    elif has_custody:
        complete = False
    return Inventory(
        venues=venues,
        btc=sum((venue_inventory.btc for venue_inventory in known), ZERO) if complete else None,
        btcb2=sum((venue_inventory.btcb2 for venue_inventory in known), ZERO) if complete else None,
        value=sum(values, ZERO) if complete and None not in values else None,  # type: ignore[arg-type]
        custody=wallet,
        custody_mid=wallet_mid,
    )
