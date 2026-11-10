"""Local validations before sending an order.

`check()` is pure: it takes the request, a venue snapshot (`RiskContext`) and the
limits, and returns *all* violations; an empty list means it can be sent. Missing or
inconsistent data (no mid, stale or crossed book) is rejected: fail closed.

Size per order (D-19): the notional cannot exceed `max_order_pct` % of the total
inventory of both venues in BTC (`RiskContext.inventory_value`, computed by the
caller with `core/inventory.py`). Without a computable inventory, reject.

Venue exposure (PA-06 → D-07): custodied value after the order, `quote total +
BTCB2 total × mid` (quote = BTC, RNF-10). Buying below the mid raises it and selling
below lowers it; fees are ignored, which overstates the value (conservative).

Spec: RF-10, CA-04 · design.md §6.3 · Tasks: T-06, T-22
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from .models import ZERO, Balance, MarketSpec, Order, OrderRequest

HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """`[risk]` section of config.toml."""

    max_order_pct: Decimal  # % of total inventory per order (D-19)
    max_mid_deviation_pct: Decimal
    max_venue_exposure: Decimal
    stale_after_s: int
    hanging_leg_after_s: int
    max_clock_skew_s: int = 5  # with the venue; above it, no live (RNF-16, D-30)
    book_check_s: int = 5  # book check against REST (RNF-24, D-76)


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Venue snapshot at validation time."""

    market: MarketSpec
    best_bid: Decimal | None
    best_ask: Decimal | None
    book_ts: int | None  # epoch ms, local arrival time of the book (RNF-16)
    now: int  # epoch ms
    open_orders: tuple[Order, ...]  # own orders; filtered by venue and status
    balances: Mapping[str, Balance]
    inventory_value: Decimal | None  # total of both venues in BTC; None = not computable

    @property
    def mid(self) -> Decimal | None:
        """None if a side is missing or the book is crossed: no reliable price."""
        if self.best_bid is None or self.best_ask is None or self.best_bid >= self.best_ask:
            return None
        return (self.best_bid + self.best_ask) / 2

    def balance(self, asset: str) -> Balance:
        return self.balances.get(asset) or Balance(asset, ZERO)


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    message: str


def check(order_request: OrderRequest, ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """All violations of `req`, in rule order; empty = it can be sent."""
    if order_request.venue != ctx.market.venue:
        raise ValueError(
            f"orden para {order_request.venue} validada con el contexto de {ctx.market.venue}"
        )
    return [
        *_stale(ctx, limits),
        *_size(order_request, ctx, limits),
        *_grid(order_request, ctx.market),
        *_mid_deviation(order_request, ctx, limits),
        *_self_cross(order_request, ctx),
        *_balance(order_request, ctx),
        *_exposure(order_request, ctx, limits),
    ]


def _stale(ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Market data: no reliable mid or stale book."""
    if ctx.mid is None:
        return [Violation("stale_data", "sin mid fiable (book vacío, incompleto o cruzado)")]
    if ctx.book_ts is None or ctx.now - ctx.book_ts > limits.stale_after_s * 1000:
        message = f"book sin actualizar hace más de {limits.stale_after_s} s"
        return [Violation("stale_data", message)]
    return []


def _size(order_request: OrderRequest, ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Size: the notional stays within `max_order_pct` % of total inventory (D-19)."""
    percent = limits.max_order_pct
    if ctx.inventory_value is None:
        message = f"no se puede calcular el inventario total para el tope del {percent} %"
        return [Violation("order_size", message)]
    notional = order_request.qty * order_request.price
    cap = ctx.inventory_value * percent / HUNDRED
    if notional <= cap:
        return []
    message = (
        f"nocional {notional:.8f} > {percent} % del inventario total "
        f"({ctx.inventory_value:.8f} {ctx.market.quote}) = {cap:.8f}"
    )
    return [Violation("order_size", message)]


def _grid(order_request: OrderRequest, market: MarketSpec) -> list[Violation]:
    """Market grid: tick, lot and minimums."""
    notional = order_request.qty * order_request.price
    out = []
    if order_request.price % market.tick_size != 0:
        out.append(
            Violation("tick", f"precio {order_request.price} fuera del tick {market.tick_size}")
        )
    if order_request.qty % market.lot_size != 0:
        out.append(
            Violation("lot", f"cantidad {order_request.qty} fuera del lote {market.lot_size}")
        )
    if order_request.qty < market.min_qty:
        message = f"cantidad {order_request.qty} < mínimo {market.min_qty} {market.base}"
        out.append(Violation("min_qty", message))
    if notional < market.min_notional:
        message = f"nocional {notional:.8f} < mínimo {market.min_notional} {market.quote}"
        out.append(Violation("min_notional", message))
    return out


def _mid_deviation(
    order_request: OrderRequest, ctx: RiskContext, limits: RiskLimits
) -> list[Violation]:
    """Price against the mid; with no mid, `_stale` already rejects."""
    mid = ctx.mid
    if mid is None:
        return []
    deviation = abs(order_request.price - mid) / mid * HUNDRED
    if deviation <= limits.max_mid_deviation_pct:
        return []
    message = f"precio a {deviation:.2f}% del mid {mid} (máx. {limits.max_mid_deviation_pct}%)"
    return [Violation("mid_deviation", message)]


def _self_cross(order_request: OrderRequest, ctx: RiskContext) -> list[Violation]:
    """Crossing own open orders on the same venue."""
    mine = [o for o in ctx.open_orders if o.venue == ctx.market.venue and not o.is_done]
    if order_request.side == "buy":
        asks = [o.price for o in mine if o.side == "sell"]
        if asks and order_request.price >= min(asks):
            message = f"compra a {order_request.price} cruza tu venta abierta a {min(asks)}"
            return [Violation("self_cross", message)]
    else:
        bids = [o.price for o in mine if o.side == "buy"]
        if bids and order_request.price <= max(bids):
            message = f"venta a {order_request.price} cruza tu compra abierta a {max(bids)}"
            return [Violation("self_cross", message)]
    return []


def _balance(order_request: OrderRequest, ctx: RiskContext) -> list[Violation]:
    """Available balance: a buy includes the taker fee."""
    market = ctx.market
    if order_request.side == "buy":
        asset = market.quote
        need = order_request.qty * order_request.price * (1 + market.taker_fee)
    else:
        asset = market.base
        need = order_request.qty
    have = ctx.balance(asset).available
    if need <= have:
        return []
    # 8 decimals: notional × (1 + fee) can come out with 19 ("0.03513485024903604")
    message = f"hace falta {need:.8f} {asset}, disponible {have:.8f}"
    return [Violation("insufficient_balance", message)]


def _exposure(order_request: OrderRequest, ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Venue exposure after the order (D-07); with no mid, `_stale` already rejects."""
    mid = ctx.mid
    if mid is None:
        return []
    market = ctx.market
    value = ctx.balance(market.quote).total + ctx.balance(market.base).total * mid
    direction = 1 if order_request.side == "buy" else -1
    after = value + direction * order_request.qty * (mid - order_request.price)
    if after <= limits.max_venue_exposure:
        return []
    message = (
        f"valor en {market.venue} tras la orden {after:.8f} > tope "
        f"{limits.max_venue_exposure} {market.quote}"
    )
    return [Violation("venue_exposure", message)]
