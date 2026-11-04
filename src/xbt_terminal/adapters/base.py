"""Venue protocol and errors common to the adapters.

`MarketData` is the public part (T-08, T-09); `Venue` adds the private one (T-17).
The UI and services only know these protocols, never a concrete adapter (RNF-09).

Public contract: docs/contrato-venue.md, checked by tests/contract/.

Spec: RF-36, RNF-09, RNF-12 · design.md §4 · Tasks: T-08, T-09, T-25, T-88
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ..core.models import (
    Balance,
    BookDelta,
    BookSnapshot,
    Candle,
    ConnState,
    DepositAddress,
    FeeUpdate,
    Fill,
    MarketSpec,
    Order,
    OrderRequest,
    PastFill,
    Timeframe,
    Trade,
    VenueId,
    WithdrawalFee,
)

MarketEvent = BookSnapshot | BookDelta | Trade | ConnState
PrivateEvent = Order | Fill | FeeUpdate | PastFill | ConnState


class VenueError(Exception):
    """The venue answered with an error or something unintelligible.

    `status`: HTTP code if there was a response. `unreadable`: the response was not
    JSON. With both, `StreamingVenue.sending` decides whether a send is uncertain.
    """

    def __init__(self, message: str, *, status: int | None = None, unreadable: bool = False):
        super().__init__(message)
        self.status = status
        self.unreadable = unreadable


def describe(exc: BaseException) -> str:
    """Text of an error, never empty: httpx's `ConnectTimeout` and others arrive with
    `str(exc) == ""`, and a blank error passed for "no error" (D-51)."""
    return str(exc).strip() or type(exc).__name__


class ReadOnlyError(VenueError):
    """Private operation without credentials."""


class UncertainError(VenueError):
    """Unknown whether the venue applied the send: the order may exist (RNF-12, D-26)."""


class Limiter(Protocol):
    async def acquire(self, kind: str) -> None: ...


@runtime_checkable
class MarketData(Protocol):
    id: VenueId

    async def market(self) -> MarketSpec: ...
    async def book_snapshot(self, depth: int) -> BookSnapshot: ...
    async def recent_trades(self, limit: int) -> list[Trade]: ...
    async def candles(self, tf: Timeframe, limit: int) -> list[Candle]: ...
    def market_stream(self) -> AsyncIterator[MarketEvent]: ...


@runtime_checkable
class Venue(MarketData, Protocol):
    async def balances(self) -> dict[str, Balance]: ...
    async def open_orders(self) -> list[Order]: ...
    async def place_limit(self, req: OrderRequest) -> Order: ...
    async def cancel(self, order_id: str) -> None: ...
    async def cancel_all(self) -> None: ...
    def private_stream(self) -> AsyncIterator[PrivateEvent]: ...

    # reconciliation of uncertain sends (RNF-12, D-26)
    keeps_client_id: bool  # does the venue return each order's client_id?

    async def recent_orders(self, since: int) -> list[Order]: ...


@runtime_checkable
class FeedUpkeep(Protocol):
    """What the services use from a real venue's adapter, when it is there (D-82).
    Apart from `Venue` because `DryRunVenue` and `ReplayVenue` are venues without it."""

    clock_skew_ms: int | None  # venue minus local, from the last response (RNF-16)

    async def resync(self) -> None: ...  # the local book does not match REST (RNF-24)
    async def aclose(self) -> None: ...


@runtime_checkable
class Depositor(Protocol):
    """Reads deposit addresses (RF-28): apart from `Venue`, read only."""

    async def deposit_address(self, asset: str) -> DepositAddress: ...


@runtime_checkable
class WithdrawalFees(Protocol):
    """Reads withdrawal fees (RF-29): read only, like `Depositor`."""

    async def withdrawal_fee(self, asset: str) -> WithdrawalFee: ...


@runtime_checkable
class TokenPricer(Protocol):
    """Public BTC price of the fee token (RF-25, D-65): read only."""

    async def token_price(self, asset: str) -> Decimal: ...
