"""DryRunVenue: market data real, órdenes simuladas en memoria.

Envuelve el adaptador real: los datos de mercado y los saldos los da él, y las
órdenes se simulan aquí (D-16). Los saldos son los reales de cada consulta más
lo simulado: al abrir una orden su importe pasa de `available` a `locked`, y al
cancelarla vuelve. La terminal no simula fills: las órdenes quedan abiertas
hasta cancelarlas. `simulate_fill` y los saldos fijos del constructor existen
solo para los tests.

Para no esperar a la red al enviar (RNF-11, D-18): el `MarketSpec` sale del hub
(`spec`) y la comprobación de saldo reutiliza la última lectura real si tiene
menos de `BASE_MAX_AGE_MS`.

Spec: RF-10, RF-13, RNF-11 · design.md §4 · Tareas: T-16, T-16b, T-18, T-21
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
        """`balances`: saldos fijos en lugar de los reales, solo para tests.
        `spec`: el MarketSpec ya cargado (el del hub), para no pedirlo por REST."""
        self.inner = inner
        self.id = inner.id
        self._fixed = dict(balances) if balances is not None else None
        # lo simulado encima de la base: activo → (Δ available, Δ locked)
        self._delta: dict[str, tuple[Decimal, Decimal]] = {}
        self._orders: dict[str, Order] = {}
        self._locks: dict[str, tuple[str, Decimal]] = {}  # order_id → (activo, importe)
        self._ids = itertools.count(1)
        self._fill_ids = itertools.count(1)
        self._clock = clock
        self._spec: MarketSpec | None = None
        self._spec_source = spec
        self._last_base: tuple[int, dict[str, Balance]] | None = None  # (hora, saldos)
        self._listeners: list[asyncio.Queue[PrivateEvent]] = []

    # -- mercado: delegado ---------------------------------------------------------

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

    # -- privado: simulado ---------------------------------------------------------

    async def _base(self, *, reuse: bool = False) -> dict[str, Balance]:
        """Saldos reales (o fijos). Con `reuse`, vale la última lectura reciente."""
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
        """Todas las simuladas, terminadas incluidas (D-26)."""
        return list(self._orders.values())

    async def place_limit(self, req: OrderRequest) -> Order:
        spec = await self._market_spec()
        if req.side == "buy":
            asset, amount = spec.quote, req.notional * (1 + spec.taker_fee)
        else:
            asset, amount = spec.base, req.qty
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
            client_id=req.client_id,
            side=req.side,
            price=req.price,
            qty=req.qty,
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

    async def simulate_fill(self, order_id: str, qty: Decimal) -> Order:
        """Llena `qty` de una orden abierta a su precio límite, con fee taker."""
        order = self._orders.get(order_id)
        if order is None or order.is_done:
            raise VenueError(f"dry-run: no hay orden abierta {order_id}")
        if qty <= 0 or qty > order.remaining:
            raise VenueError(f"dry-run: a {order_id} le queda {order.remaining:f}, no {qty:f}")
        spec = await self._market_spec()
        gross = qty * order.price
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
        else:
            self._locks[order_id] = (lock_asset, locked - used)
        self._publish(updated)
        trade_id = f"dry-fill-{next(self._fill_ids)}"
        self._publish(
            Fill(self.id, trade_id, order_id, order.side, order.price, qty, fee, spec.quote,
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
