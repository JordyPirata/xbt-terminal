"""Cantidad de una orden a partir de un porcentaje del saldo del venue (RF-17, D-21).

La base es el saldo total (disponible + bloqueado) del venue de la orden:
- compra: `qty = N % × BTC / (precio × (1 + fee taker))`, para que la fee quepa;
- venta: `qty = N % × BTCB2`.
Las dos, hacia abajo al lote. Sin saldo, o con menos de un lote, sale 0 y quien
llama decide (OrderService lo rechaza). Las reglas de RF-10 no se miran aquí.

Spec: RF-17 · Tarea: T-23
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
    basis: Decimal  # saldo total del que sale el porcentaje
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
