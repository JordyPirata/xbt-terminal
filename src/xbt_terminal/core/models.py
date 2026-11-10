"""Domain models in Decimal: MarketSpec, Level, BookSnapshot, Order, Fill...

All amounts are `Decimal`, checked at construction: a `float` raises TypeError
instead of slipping into a price. Timestamps are epoch milliseconds UTC (`int`),
which both venues return and which group unambiguously.

Spec: RNF-07 · design.md §3 · Tasks: T-02, T-92
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

# Display name of each asset (D-48). Presentation only: symbols, balance keys,
# config and journal keep the venue ticker.
ASSET_LABELS = {"BTCB2": "XBT"}
BASE_LABEL = ASSET_LABELS["BTCB2"]


def asset_label(asset: str) -> str:
    """Asset name in the UI and messages."""
    return ASSET_LABELS.get(asset, asset)


def D(value: Decimal | str | int) -> Decimal:  # noqa: N802 — idioma para Decimal
    """Build a Decimal rejecting float, which would already carry binary error."""
    if isinstance(value, float):
        raise TypeError(f"float no permitido en importes: {value!r}; usar str")
    return value if isinstance(value, Decimal) else Decimal(value)


_DECIMAL_FIELDS: dict[type, tuple[str, ...]] = {}


def _check_decimals(obj: object) -> None:
    """Every `Decimal` annotation of the dataclass must hold a real Decimal."""
    cls = type(obj)
    names = _DECIMAL_FIELDS.get(cls)
    if names is None:
        # `fields()` per instance was most of the bot's cost per book change (T-92)
        names = tuple(f.name for f in fields(obj) if f.type in ("Decimal", Decimal))  # type: ignore[arg-type]
        _DECIMAL_FIELDS[cls] = names
    for name in names:
        value = getattr(obj, name)
        if not isinstance(value, Decimal):
            raise TypeError(f"{cls.__name__}.{name} debe ser Decimal, no {type(value).__name__}")


def _snap(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    """Bring `value` to a multiple of `step` in the given direction, with step's exponent."""
    units = (value / step).to_integral_value(rounding=rounding)
    return (units * step).quantize(step)


@dataclass(frozen=True, slots=True)
class MarketSpec:
    """Market rules. Read from the venue at startup, never hardcoded."""

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
        """Round to the tick in the direction that does not leave the order behind what
        was asked: a buy up, a sell down."""
        if side == "buy":
            return _snap(price, self.tick_size, ROUND_CEILING)
        if side == "sell":
            return _snap(price, self.tick_size, ROUND_FLOOR)
        raise ValueError(f"lado inválido: {side!r}")

    def quantize_qty(self, quantity: Decimal) -> Decimal:
        """Truncate to the lot: never send more than asked."""
        return _snap(quantity, self.lot_size, ROUND_FLOOR)


@dataclass(frozen=True, slots=True)
class Level:
    price: Decimal
    qty: Decimal
    amm: bool = False  # Neoxa mixes AMM liquidity into the book

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
    """Level changes; qty == 0 deletes the level."""

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
    side: Side  # taker side
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
    """Deposit address of a pair asset on a venue (RF-28). `asset` is the pair's (BTC,
    BTCB2) and `ticker` the venue's name for it."""

    venue: VenueId
    asset: str
    ticker: str
    network: str
    address: str
    memo: str = ""  # payment id or tag, if the venue asks for it
    min_deposit: Decimal | None = None
    confirmations: int | None = None


@dataclass(frozen=True, slots=True)
class DepositRow:
    """A row of the deposits window (RF-28): the address or why it did not arrive."""

    venue: VenueId
    asset: str
    address: DepositAddress | None
    error: str = ""


@dataclass(frozen=True, slots=True)
class WithdrawalFee:
    """Withdrawal fee of a pair asset on a venue (RF-29), in that asset. `minimum` is
    the minimum withdrawal, if the venue gives it; `active` is false if withdrawals
    are closed or under maintenance. `on_top`: the fee is charged on top of the
    amount (the amount arrives) instead of deducted from it (D-53)."""

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
    """Own execution. Neoxa does not say which order it belongs to: `order_id` None (D-14)."""

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
    """A trade already on the venue at startup (D-68): not a new fill for legs or bot,
    but the account compares it with the journal."""

    fill: Fill


@dataclass(frozen=True, slots=True)
class FeeUpdate:
    """The venue changed the fee of an already reported fill (NonKYC redoes it in NKYC,
    D-64). The quantity does not change: it is not another fill."""

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
    """State of a connection for the RF-14 indicator."""

    venue: VenueId
    channel: Channel
    source: Source  # "rest" = fallback polling (RF-01)
    status: ConnStatus
    latency_ms: float | None
    since: int
