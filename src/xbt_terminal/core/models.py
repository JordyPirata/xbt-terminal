"""Modelos de dominio en Decimal: MarketSpec, Level, BookSnapshot, Order, Fill...

Todos los importes son `Decimal` y se comprueba al construir: un `float` lanza
TypeError en vez de colarse en un precio. Los timestamps son milisegundos epoch
UTC (`int`), que es lo que devuelven ambos venues y se agrupa sin ambigüedad.

Spec: RNF-07 · design.md §3 · Tarea: T-02
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

VenueId = Literal["nk", "nx"]
Side = Literal["buy", "sell"]
Timeframe = Literal["1m", "5m", "1h", "4h", "1d"]
OrderStatus = Literal["pending", "open", "partial", "filled", "cancelled", "rejected"]
Channel = Literal["market", "private"]
Source = Literal["ws", "rest"]
ConnStatus = Literal["up", "reconnecting", "down"]

ZERO = Decimal(0)
DONE_STATUSES = frozenset({"filled", "cancelled", "rejected"})

# Nombre que se muestra de cada activo (D-48). Solo presentación: símbolos,
# claves de saldo, config y journal siguen con el ticker del venue.
ASSET_LABELS = {"BTCB2": "XBT"}
BASE_LABEL = ASSET_LABELS["BTCB2"]


def asset_label(asset: str) -> str:
    """Nombre del activo en la UI y los mensajes."""
    return ASSET_LABELS.get(asset, asset)


def D(value: Decimal | str | int) -> Decimal:
    """Construye un Decimal rechazando float, que ya llegaría con error binario."""
    if isinstance(value, float):
        raise TypeError(f"float no permitido en importes: {value!r}; usar str")
    return value if isinstance(value, Decimal) else Decimal(value)


def _check_decimals(obj: object) -> None:
    """Toda anotación `Decimal` del dataclass debe contener un Decimal de verdad."""
    for f in fields(obj):  # type: ignore[arg-type]
        if f.type in ("Decimal", Decimal):
            value = getattr(obj, f.name)
            if not isinstance(value, Decimal):
                raise TypeError(
                    f"{type(obj).__name__}.{f.name} debe ser Decimal, no {type(value).__name__}"
                )


def _snap(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    """Lleva `value` al múltiplo de `step` en la dirección dada, con el exponente de step."""
    units = (value / step).to_integral_value(rounding=rounding)
    return (units * step).quantize(step)


@dataclass(frozen=True, slots=True)
class MarketSpec:
    """Reglas del mercado. Se leen del venue al arrancar, no se hardcodean."""

    venue: VenueId
    symbol: str
    base: str
    quote: str
    tick_size: Decimal
    lot_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    maker_fee: Decimal
    taker_fee: Decimal

    def __post_init__(self) -> None:
        _check_decimals(self)
        if self.tick_size <= 0 or self.lot_size <= 0:
            raise ValueError(f"{self.venue}: tick_size y lot_size deben ser > 0")

    def quantize_price(self, price: Decimal, side: Side) -> Decimal:
        """Redondea al tick en la dirección que no deja la orden por detrás de lo
        pedido: una compra hacia arriba, una venta hacia abajo."""
        if side == "buy":
            return _snap(price, self.tick_size, ROUND_CEILING)
        if side == "sell":
            return _snap(price, self.tick_size, ROUND_FLOOR)
        raise ValueError(f"lado inválido: {side!r}")

    def quantize_qty(self, qty: Decimal) -> Decimal:
        """Trunca al lote: nunca se envía más cantidad de la pedida."""
        return _snap(qty, self.lot_size, ROUND_FLOOR)


@dataclass(frozen=True, slots=True)
class Level:
    price: Decimal
    qty: Decimal
    amm: bool = False  # Neoxa mezcla liquidez de AMM en el book

    def __post_init__(self) -> None:
        _check_decimals(self)


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    venue: VenueId
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    ts: int
    seq: int | None = None


@dataclass(frozen=True, slots=True)
class BookDelta:
    """Cambios de nivel; qty == 0 borra el nivel."""

    venue: VenueId
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    ts: int
    seq: int | None = None


@dataclass(frozen=True, slots=True)
class Trade:
    venue: VenueId
    price: Decimal
    qty: Decimal
    side: Side  # lado del taker
    ts: int

    def __post_init__(self) -> None:
        _check_decimals(self)


@dataclass(frozen=True, slots=True)
class Candle:
    ts_open: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        _check_decimals(self)


@dataclass(frozen=True, slots=True)
class DepositAddress:
    """Dirección de depósito de un activo del par en un venue (RF-28). `asset` es
    el del par (BTC, BTCB2) y `ticker` el nombre que le da el venue."""

    venue: VenueId
    asset: str
    ticker: str
    network: str
    address: str
    memo: str = ""  # payment id o tag, si el venue lo pide
    min_deposit: Decimal | None = None
    confirmations: int | None = None


@dataclass(frozen=True, slots=True)
class DepositRow:
    """Una fila de la ventana de depósitos (RF-28): la dirección o por qué no llegó."""

    venue: VenueId
    asset: str
    address: DepositAddress | None
    error: str = ""


@dataclass(frozen=True, slots=True)
class WithdrawalFee:
    """Comisión de retiro de un activo del par en un venue (RF-29), en ese
    activo. `minimum` es el retiro mínimo, si el venue lo da; `active` es falso
    si los retiros están cerrados o en mantenimiento. `on_top`: la comisión se
    cobra aparte de lo pedido (llega lo pedido) en vez de descontarse (D-53)."""

    venue: VenueId
    asset: str
    ticker: str
    fee: Decimal
    minimum: Decimal | None = None
    active: bool = True
    on_top: bool = False

    def __post_init__(self) -> None:
        _check_decimals(self)


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    available: Decimal
    locked: Decimal = ZERO

    def __post_init__(self) -> None:
        _check_decimals(self)

    @property
    def total(self) -> Decimal:
        return self.available + self.locked


@dataclass(frozen=True, slots=True)
class OrderRequest:
    venue: VenueId
    side: Side
    qty: Decimal
    price: Decimal
    client_id: str

    def __post_init__(self) -> None:
        _check_decimals(self)

    @property
    def notional(self) -> Decimal:
        return self.qty * self.price


@dataclass(frozen=True, slots=True)
class Order:
    venue: VenueId
    order_id: str
    client_id: str | None
    side: Side
    price: Decimal
    qty: Decimal
    filled: Decimal
    status: OrderStatus
    ts: int

    def __post_init__(self) -> None:
        _check_decimals(self)

    @property
    def remaining(self) -> Decimal:
        return max(self.qty - self.filled, ZERO)

    @property
    def is_done(self) -> bool:
        return self.status in DONE_STATUSES


@dataclass(frozen=True, slots=True)
class Fill:
    """Ejecución propia. Neoxa no dice a qué orden pertenece: `order_id` None (D-14)."""

    venue: VenueId
    trade_id: str
    order_id: str | None
    side: Side
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_asset: str | None
    ts: int

    def __post_init__(self) -> None:
        _check_decimals(self)


@dataclass(frozen=True, slots=True)
class PastFill:
    """Un trade que ya estaba en el venue al arrancar (D-68): no es un fill nuevo
    para piernas ni bot, pero la cuenta lo compara con el journal."""

    fill: Fill


@dataclass(frozen=True, slots=True)
class FeeUpdate:
    """El venue cambió la fee de un fill ya avisado (NonKYC la rehace en NKYC,
    D-64). La cantidad no cambia: no es otro fill."""

    venue: VenueId
    trade_id: str
    order_id: str | None
    fee: Decimal
    fee_asset: str | None
    ts: int

    def __post_init__(self) -> None:
        _check_decimals(self)


@dataclass(frozen=True, slots=True)
class ConnState:
    """Estado de una conexión para el indicador de RF-14."""

    venue: VenueId
    channel: Channel
    source: Source  # "rest" = polling de respaldo (RF-01)
    status: ConnStatus
    latency_ms: float | None
    since: int
