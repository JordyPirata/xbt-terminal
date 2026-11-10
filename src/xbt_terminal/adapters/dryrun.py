"""DryRunVenue: real market data, orders simulated in memory.

Wraps the real adapter: it gives market data and balances, and orders are simulated
here (D-16). Balances are the real ones of each query plus the simulated: opening
an order moves its amount from `available` to `locked`, and cancelling moves it
back. The terminal does not simulate fills: orders stay open until cancelled.
`simulate_fill` and the constructor's fixed balances are for tests and for
`SimVenue`, which replays a recording (RF-34).

To avoid waiting on the network when sending (RNF-11, D-18): the `MarketSpec` comes
from the hub (`spec`) and the balance check reuses the last real read if it is
younger than `BASE_MAX_AGE_MS`.

Spec: RF-10, RF-13, RNF-11 · design.md §4 · Tasks: T-16, T-16b, T-18, T-21
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from decimal import Decimal

from ..core.models import (
    ZERO,
    Balance,
    BookSnapshot,
    Candle,
    Fill,
    MarketSpec,
    Order,
    OrderRequest,
    Timeframe,
    Trade,
)
from .base import MarketData, MarketEvent, PrivateEvent, ReadOnlyError, VenueError
from .streaming import now_ms

BASE_MAX_AGE_MS = 30_000


class DryRunVenue:
    keeps_client_id = True

    def __init__(
        self,
        inner: MarketData,
        balances: Mapping[str, Balance] | None = None,
        *,
        spec: Callable[[], MarketSpec | None] | None = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        """`balances`: fixed balances instead of the real ones (tests and `SimVenue`).
        `spec`: the MarketSpec already loaded (the hub's), to avoid asking over REST."""
        self.inner = inner
        self.id = inner.id
        self._fixed = dict(balances) if balances is not None else None
        # simulated on top of the base: asset → (Δ available, Δ locked)
        self._delta: dict[str, tuple[Decimal, Decimal]] = {}
        self._orders: dict[str, Order] = {}
        self._locks: dict[str, tuple[str, Decimal]] = {}  # order_id → (asset, amount)
        self._ids = itertools.count(1)
        self._fill_ids = itertools.count(1)
        self._clock = clock
        self._spec: MarketSpec | None = None
        self._spec_source = spec
        self._last_base: tuple[int, dict[str, Balance]] | None = None  # (time, balances)
        self._listeners: list[asyncio.Queue[PrivateEvent]] = []

    # -- market: delegated ---------------------------------------------------------

    async def market(self) -> MarketSpec:
        self._spec = await self.inner.market()
        return self._spec

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        return await self.inner.book_snapshot(depth)

    async def recent_trades(self, limit: int) -> list[Trade]:
        return await self.inner.recent_trades(limit)

    async def candles(self, tf: Timeframe, limit: int) -> list[Candle]:
        return await self.inner.candles(tf, limit)

    def market_stream(self) -> AsyncIterator[MarketEvent]:
        return self.inner.market_stream()

    # -- private: simulated --------------------------------------------------------

    async def _base(self, *, reuse: bool = False) -> dict[str, Balance]:
        """Real (or fixed) balances. With `reuse`, the last recent read will do."""
        if self._fixed is not None:
            return dict(self._fixed)
        if reuse and self._last_base is not None:
            at, base = self._last_base
            if self._clock() - at <= BASE_MAX_AGE_MS:
                return dict(base)
        read = getattr(self.inner, "balances", None)
        if read is None:
            raise ReadOnlyError(f"dry-run {self.id}: el venue no tiene parte privada")
        base = dict(await read())
        self._last_base = (self._clock(), base)
        return dict(base)

    async def _market_spec(self) -> MarketSpec:
        known = self._spec_source() if self._spec_source is not None else None
        return known or self._spec or await self.market()

    async def balances(self, *, reuse: bool = False) -> dict[str, Balance]:
        out = await self._base(reuse=reuse)
        for asset, (d_avail, d_locked) in self._delta.items():
            bal = out.get(asset) or Balance(asset, ZERO)
            out[asset] = Balance(asset, bal.available + d_avail, bal.locked + d_locked)
        return out

    async def open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if not o.is_done]

    async def recent_orders(self, since: int) -> list[Order]:
        """All simulated ones, finished included (D-26)."""
        return list(self._orders.values())

    async def place_limit(self, order_request: OrderRequest) -> Order:
        spec = await self._market_spec()
        if order_request.side == "buy":
            asset, amount = spec.quote, order_request.notional * (1 + spec.taker_fee)
        else:
            asset, amount = spec.base, order_request.qty
        bal = (await self.balances(reuse=True)).get(asset) or Balance(asset, ZERO)
        if amount > bal.available:
            raise VenueError(
                f"dry-run: saldo insuficiente: hacen falta {amount:f} {asset}, "
                f"hay {bal.available:f}"
            )
        self._move(asset, available=-amount, locked=amount)
        order = Order(
            venue=self.id,
            order_id=f"dry-{self.id}-{next(self._ids)}",
            client_id=order_request.client_id,
            side=order_request.side,
            price=order_request.price,
            qty=order_request.qty,
            filled=ZERO,
            status="open",
            ts=self._clock(),
        )
        self._orders[order.order_id] = order
        self._locks[order.order_id] = (asset, amount)
        self._publish(order)
        return order

    async def cancel(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order is None or order.is_done:
            raise VenueError(f"dry-run: no hay orden abierta {order_id}")
        asset, amount = self._locks.pop(order_id)
        self._move(asset, available=amount, locked=-amount)
        cancelled = replace(order, status="cancelled", ts=self._clock())
        self._orders[order_id] = cancelled
        self._publish(cancelled)

    async def cancel_all(self) -> None:
        for order in await self.open_orders():
            await self.cancel(order.order_id)

    async def simulate_fill(
        self, order_id: str, qty: Decimal, *, price: Decimal | None = None
    ) -> Order:
        """Fill `qty` of an open order with taker fee, at `price` or, without it, at its
        limit price. When fully filled, what was over-locked comes back."""
        order = self._orders.get(order_id)
        if order is None or order.is_done:
            raise VenueError(f"dry-run: no hay orden abierta {order_id}")
        if qty <= 0 or qty > order.remaining:
            raise VenueError(f"dry-run: a {order_id} le queda {order.remaining:f}, no {qty:f}")
        spec = await self._market_spec()
        price = order.price if price is None else price
        gross = qty * price
        fee = gross * spec.taker_fee
        lock_asset, locked = self._locks[order_id]
        if order.side == "buy":
            used = gross + fee
            self._move(lock_asset, locked=-used)
            self._move(spec.base, available=qty)
        else:
            used = qty
            self._move(lock_asset, locked=-used)
            self._move(spec.quote, available=gross - fee)
        filled = order.filled + qty
        status = "filled" if filled == order.qty else "partial"
        updated = replace(order, filled=filled, status=status, ts=self._clock())
        self._orders[order_id] = updated
        if status == "filled":
            self._locks.pop(order_id)
            spare = locked - used
            self._move(lock_asset, available=spare, locked=-spare)
        else:
            self._locks[order_id] = (lock_asset, locked - used)
        self._publish(updated)
        trade_id = f"dry-fill-{next(self._fill_ids)}"
        self._publish(
            Fill(self.id, trade_id, order_id, order.side, price, qty, fee, spec.quote,
                 self._clock())
        )  # fmt: skip
        return updated

    def _move(self, asset: str, *, available: Decimal = ZERO, locked: Decimal = ZERO) -> None:
        d_avail, d_locked = self._delta.get(asset, (ZERO, ZERO))
        self._delta[asset] = (d_avail + available, d_locked + locked)

    async def private_stream(self) -> AsyncIterator[PrivateEvent]:
        queue: asyncio.Queue[PrivateEvent] = asyncio.Queue()
        self._listeners.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._listeners.remove(queue)

    def _publish(self, event: PrivateEvent) -> None:
        for queue in self._listeners:
            queue.put_nowait(event)
