"""Validaciones locales antes de enviar una orden.

`check()` es una función pura: recibe la petición, una foto del venue
(`RiskContext`) y los límites, y devuelve *todas* las violaciones; lista vacía
significa que se puede enviar. Ante datos que faltan o no cuadran (sin mid,
book viejo o cruzado) se rechaza: fallar cerrado.

Tamaño por orden (D-19): el nocional no puede pasar de `max_order_pct` % del
inventario total de los dos venues en BTC (`RiskContext.inventory_value`, lo
calcula quien llama con `core/inventory.py`). Sin inventario calculable, rechazo.

Exposición por venue (PA-06 → D-07): valor custodiado tras la orden,
`quote total + BTCB2 total × mid` (quote = BTC, RNF-10). Comprar por debajo del mid lo sube y vender
por debajo lo baja; las fees se ignoran, lo que sobrestima el valor (conservador).

Spec: RF-10, CA-04 · design.md §6.3 · Tareas: T-06, T-22
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from .models import ZERO, Balance, MarketSpec, Order, OrderRequest

HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Sección `[risk]` de config.toml."""

    max_order_pct: Decimal  # % del inventario total por orden (D-19)
    max_mid_deviation_pct: Decimal
    max_venue_exposure: Decimal
    stale_after_s: int
    hanging_leg_after_s: int
    max_clock_skew_s: int = 5  # con el venue; por encima, sin live (RNF-16, D-30)
    book_check_s: int = 5  # contraste del book con el REST (RNF-24, D-76)


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Foto de un venue en el momento de validar."""

    market: MarketSpec
    best_bid: Decimal | None
    best_ask: Decimal | None
    book_ts: int | None  # ms epoch, hora local a la que llegó el book (RNF-16)
    now: int  # ms epoch
    open_orders: tuple[Order, ...]  # propias; se filtran por venue y estado
    balances: Mapping[str, Balance]
    inventory_value: Decimal | None  # total de los dos venues en BTC; None = no calculable

    @property
    def mid(self) -> Decimal | None:
        """None si falta un lado o el book está cruzado: no hay precio fiable."""
        if self.best_bid is None or self.best_ask is None or self.best_bid >= self.best_ask:
            return None
        return (self.best_bid + self.best_ask) / 2

    def balance(self, asset: str) -> Balance:
        return self.balances.get(asset) or Balance(asset, ZERO)


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    message: str


def check(req: OrderRequest, ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Todas las violaciones de `req`, en el orden de las reglas; vacía = se puede enviar."""
    if req.venue != ctx.market.venue:
        raise ValueError(f"orden para {req.venue} validada con el contexto de {ctx.market.venue}")
    return [
        *_stale(ctx, limits),
        *_size(req, ctx, limits),
        *_grid(req, ctx.market),
        *_mid_deviation(req, ctx, limits),
        *_self_cross(req, ctx),
        *_balance(req, ctx),
        *_exposure(req, ctx, limits),
    ]


def _stale(ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Datos de mercado: sin mid fiable o book sin actualizar."""
    if ctx.mid is None:
        return [Violation("stale_data", "sin mid fiable (book vacío, incompleto o cruzado)")]
    if ctx.book_ts is None or ctx.now - ctx.book_ts > limits.stale_after_s * 1000:
        message = f"book sin actualizar hace más de {limits.stale_after_s} s"
        return [Violation("stale_data", message)]
    return []


def _size(req: OrderRequest, ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Tamaño: el nocional no pasa del `max_order_pct` % del inventario total (D-19)."""
    pct = limits.max_order_pct
    if ctx.inventory_value is None:
        message = f"no se puede calcular el inventario total para el tope del {pct} %"
        return [Violation("order_size", message)]
    notional = req.qty * req.price
    cap = ctx.inventory_value * pct / HUNDRED
    if notional <= cap:
        return []
    message = (
        f"nocional {notional:.8f} > {pct} % del inventario total "
        f"({ctx.inventory_value:.8f} {ctx.market.quote}) = {cap:.8f}"
    )
    return [Violation("order_size", message)]


def _grid(req: OrderRequest, market: MarketSpec) -> list[Violation]:
    """Rejilla del mercado: tick, lote y mínimos."""
    notional = req.qty * req.price
    out = []
    if req.price % market.tick_size != 0:
        out.append(Violation("tick", f"precio {req.price} fuera del tick {market.tick_size}"))
    if req.qty % market.lot_size != 0:
        out.append(Violation("lot", f"cantidad {req.qty} fuera del lote {market.lot_size}"))
    if req.qty < market.min_qty:
        message = f"cantidad {req.qty} < mínimo {market.min_qty} {market.base}"
        out.append(Violation("min_qty", message))
    if notional < market.min_notional:
        message = f"nocional {notional:.8f} < mínimo {market.min_notional} {market.quote}"
        out.append(Violation("min_notional", message))
    return out


def _mid_deviation(req: OrderRequest, ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Precio contra el mid; sin mid ya lo rechaza `_stale`."""
    mid = ctx.mid
    if mid is None:
        return []
    deviation = abs(req.price - mid) / mid * HUNDRED
    if deviation <= limits.max_mid_deviation_pct:
        return []
    message = f"precio a {deviation:.2f}% del mid {mid} (máx. {limits.max_mid_deviation_pct}%)"
    return [Violation("mid_deviation", message)]


def _self_cross(req: OrderRequest, ctx: RiskContext) -> list[Violation]:
    """Cruce con órdenes propias abiertas en el mismo venue."""
    mine = [o for o in ctx.open_orders if o.venue == ctx.market.venue and not o.is_done]
    if req.side == "buy":
        asks = [o.price for o in mine if o.side == "sell"]
        if asks and req.price >= min(asks):
            message = f"compra a {req.price} cruza tu venta abierta a {min(asks)}"
            return [Violation("self_cross", message)]
    else:
        bids = [o.price for o in mine if o.side == "buy"]
        if bids and req.price <= max(bids):
            message = f"venta a {req.price} cruza tu compra abierta a {max(bids)}"
            return [Violation("self_cross", message)]
    return []


def _balance(req: OrderRequest, ctx: RiskContext) -> list[Violation]:
    """Saldo disponible: la compra incluye la fee taker."""
    market = ctx.market
    if req.side == "buy":
        asset = market.quote
        need = req.qty * req.price * (1 + market.taker_fee)
    else:
        asset = market.base
        need = req.qty
    have = ctx.balance(asset).available
    if need <= have:
        return []
    # 8 decimales: nocional × (1 + fee) puede salir con 19 ("0.03513485024903604")
    message = f"hace falta {need:.8f} {asset}, disponible {have:.8f}"
    return [Violation("insufficient_balance", message)]


def _exposure(req: OrderRequest, ctx: RiskContext, limits: RiskLimits) -> list[Violation]:
    """Exposición del venue tras la orden (D-07); sin mid ya lo rechaza `_stale`."""
    mid = ctx.mid
    if mid is None:
        return []
    market = ctx.market
    value = ctx.balance(market.quote).total + ctx.balance(market.base).total * mid
    direction = 1 if req.side == "buy" else -1
    after = value + direction * req.qty * (mid - req.price)
    if after <= limits.max_venue_exposure:
        return []
    message = (
        f"valor en {market.venue} tras la orden {after:.8f} > tope "
        f"{limits.max_venue_exposure} {market.quote}"
    )
    return [Violation("venue_exposure", message)]
