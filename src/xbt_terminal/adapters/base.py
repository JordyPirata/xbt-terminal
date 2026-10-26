"""Protocol Venue y errores comunes de los adaptadores.

`MarketData` es la parte pública (T-08, T-09); `Venue` añade la privada (T-17).
La UI y los servicios solo conocen estos protocolos, nunca un adaptador concreto
(RNF-09).

Spec: RNF-09, RNF-12 · design.md §4 · Tareas: T-08, T-09, T-25
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
    """El venue respondió con un error o con algo que no se entiende.

    `status`: código HTTP si hubo respuesta. `unreadable`: la respuesta no era
    JSON. Con los dos, `StreamingVenue.sending` decide si un envío es incierto.
    """

    def __init__(self, message: str, *, status: int | None = None, unreadable: bool = False):
        super().__init__(message)
        self.status = status
        self.unreadable = unreadable


def describe(exc: BaseException) -> str:
    """Texto de un error, nunca vacío: `ConnectTimeout` y otras de httpx llegan
    con `str(exc) == ""`, y un error en blanco pasaba por "sin error" (D-51)."""
    return str(exc).strip() or type(exc).__name__


class ReadOnlyError(VenueError):
    """Operación privada sin credenciales."""


class UncertainError(VenueError):
    """No se sabe si el venue aplicó el envío: la orden puede existir (RNF-12, D-26)."""


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

    # reconciliación de envíos inciertos (RNF-12, D-26)
    keeps_client_id: bool  # ¿devuelve el venue el client_id de cada orden?

    async def recent_orders(self, since: int) -> list[Order]: ...


@runtime_checkable
class Depositor(Protocol):
    """Lectura de direcciones de depósito (RF-28): aparte de `Venue`, solo lee."""

    async def deposit_address(self, asset: str) -> DepositAddress: ...


@runtime_checkable
class WithdrawalFees(Protocol):
    """Lectura de comisiones de retiro (RF-29): solo lee, como `Depositor`."""

    async def withdrawal_fee(self, asset: str) -> WithdrawalFee: ...


@runtime_checkable
class TokenPricer(Protocol):
    """Precio público en BTC del token de fee (RF-25, D-65): solo lee."""

    async def token_price(self, asset: str) -> Decimal: ...
