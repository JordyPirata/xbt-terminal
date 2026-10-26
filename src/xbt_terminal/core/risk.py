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
    m = ctx.market
    if req.venue != m.venue:
        raise ValueError(f"orden para {req.venue} validada con el contexto de {m.venue}")

    out: list[Violation] = []

    def bad(code: str, message: str) -> None:
        out.append(Violation(code, message))

    notional = req.qty * req.price
    mid = ctx.mid

    # --- datos de mercado ---------------------------------------------------
    if mid is None:
        bad("stale_data", "sin mid fiable (book vacío, incompleto o cruzado)")
    elif ctx.book_ts is None or ctx.now - ctx.book_ts > limits.stale_after_s * 1000:
        bad("stale_data", f"book sin actualizar hace más de {limits.stale_after_s} s")

    # --- tamaño -------------------------------------------------------------
    pct = limits.max_order_pct
    if ctx.inventory_value is None:
        bad("order_size", f"no se puede calcular el inventario total para el tope del {pct} %")
    else:
        cap = ctx.inventory_value * pct / HUNDRED
        if notional > cap:
            bad(
                "order_size",
                f"nocional {notional:.8f} > {pct} % del inventario total "
                f"({ctx.inventory_value:.8f} {m.quote}) = {cap:.8f}",
            )

    # --- rejilla del mercado ------------------------------------------------
    if req.price % m.tick_size != 0:
        bad("tick", f"precio {req.price} fuera del tick {m.tick_size}")
    if req.qty % m.lot_size != 0:
        bad("lot", f"cantidad {req.qty} fuera del lote {m.lot_size}")
    if req.qty < m.min_qty:
        bad("min_qty", f"cantidad {req.qty} < mínimo {m.min_qty} {m.base}")
    if notional < m.min_notional:
        bad("min_notional", f"nocional {notional:.8f} < mínimo {m.min_notional} {m.quote}")

    # --- precio contra el mid ------------------------------------------------
    if mid is not None:
        deviation = abs(req.price - mid) / mid * HUNDRED
        if deviation > limits.max_mid_deviation_pct:
            bad(
                "mid_deviation",
                f"precio a {deviation:.2f}% del mid {mid} (máx. {limits.max_mid_deviation_pct}%)",
            )

    # --- cruce con órdenes propias -------------------------------------------
    mine = [o for o in ctx.open_orders if o.venue == m.venue and not o.is_done]
    if req.side == "buy":
        asks = [o.price for o in mine if o.side == "sell"]
        if asks and req.price >= min(asks):
            bad("self_cross", f"compra a {req.price} cruza tu venta abierta a {min(asks)}")
    else:
        bids = [o.price for o in mine if o.side == "buy"]
        if bids and req.price <= max(bids):
            bad("self_cross", f"venta a {req.price} cruza tu compra abierta a {max(bids)}")

    # --- saldo --------------------------------------------------------------
    if req.side == "buy":
        need, have, asset = notional * (1 + m.taker_fee), ctx.balance(m.quote).available, m.quote
    else:
        need, have, asset = req.qty, ctx.balance(m.base).available, m.base
    if need > have:
        # 8 decimales: nocional × (1 + fee) puede salir con 19 ("0.03513485024903604")
        bad("insufficient_balance", f"hace falta {need:.8f} {asset}, disponible {have:.8f}")

    # --- exposición del venue (D-07) -----------------------------------------
    if mid is not None:
        value = ctx.balance(m.quote).total + ctx.balance(m.base).total * mid
        direction = 1 if req.side == "buy" else -1
        after = value + direction * req.qty * (mid - req.price)
        if after > limits.max_venue_exposure:
            bad(
                "venue_exposure",
                f"valor en {m.venue} tras la orden {after:.8f} > tope "
                f"{limits.max_venue_exposure} {m.quote}",
            )

    return out
