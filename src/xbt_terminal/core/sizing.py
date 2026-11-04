"""Order quantity from a percentage of the venue balance (RF-17, D-21).

The base is the venue's total balance (available + locked):
- buy: `qty = N % × BTC / (price × (1 + taker fee))`, so the fee fits;
- sell: `qty = N % × BTCB2`.
Both rounded down to the lot. With no balance, or less than a lot, it gives 0 and
the caller decides (OrderService rejects). RF-10 rules are not looked at here.

Spec: RF-17 · Task: T-23
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .models import ZERO, Balance, MarketSpec, Side, _snap

HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class PercentSize:
    qty: Decimal
    pct: Decimal
    basis: Decimal  # total balance the percentage comes from
    asset: str


def size_from_percent(
    side: Side, pct: Decimal, price: Decimal, balances: Mapping[str, Balance], spec: MarketSpec
) -> PercentSize:
    asset = spec.quote if side == "buy" else spec.base
    bal = balances.get(asset)
    basis = bal.total if bal is not None else ZERO
    amount = basis * pct / HUNDRED
    if side == "buy":
        amount /= price * (1 + spec.taker_fee)
    return PercentSize(_snap(amount, spec.lot_size, ROUND_DOWN), pct, basis, asset)
