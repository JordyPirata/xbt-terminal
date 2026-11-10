"""Tracking of own orders and fills, common to both venues (D-14).

Each refresh asks REST for active orders and the latest own trades, and
`OrderTracker` takes the difference with the previous one:

- A new or changed order (filled quantity, status) comes out as `Order`.
- A new own trade comes out as `Fill`. Those of the first refresh are history and
  are not reported.
- A known trade returning with another fee or fee asset comes out as `FeeUpdate`
  (NonKYC redoes the fee in NKYC, D-64).
- An order no longer active is queried apart until it is known how it ended (filled
  or cancelled); until then it stays pending.

`poll_private` repeats the refresh every `every_s()` or sooner if someone sets
`wake` (NonKYC's private WS on a report), and emits a private channel `ConnState`
whenever state or source change. `fan_in` joins several workers into one stream and
re-raises their bugs in the consumer.

Spec: RF-08, RF-09, RF-11, RF-14, RNF-04 · design.md §4, §6.5 · Tasks: T-17, T-59
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import httpx

from ..core.models import (
    ConnState,
    ConnStatus,
    FeeUpdate,
    Fill,
    Order,
    PastFill,
    Source,
    VenueId,
)
from .base import PrivateEvent, VenueError
from .streaming import SeenIds


class OrderTracker:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}  # active, and those that disappeared unresolved
        self._sent: dict[str, Order] = {}  # sent by the terminal, not seen yet (D-41)
        self._client_ids: dict[str, str] = {}
        self._seen = SeenIds()
        self._fees: dict[str, tuple[Decimal, str | None]] = {}  # of the last refresh
        self._seeded = False

    @property
    def open(self) -> list[Order]:
        sent = [o for oid, o in self._sent.items() if oid not in self._orders]
        return [o for o in [*self._orders.values(), *sent] if not o.is_done]

    def remember(self, order: Order) -> None:
        """An order just sent by the terminal: keeps its client_id and, if alive, tracks it
        from now. If it fills before the next refresh, it will never show as active; this
        way it shows as disappeared and is queried (D-41). If it shows active, it is
        reported like any new order: the account needs it."""
        if order.client_id:
            self._client_ids[order.order_id] = order.client_id
        if not order.is_done:
            self._sent[order.order_id] = self._with_client_id(order)

    def _with_client_id(self, order: Order) -> Order:
        cid = self._client_ids.get(order.order_id)
        return replace(order, client_id=cid) if cid and not order.client_id else order

    def update(
        self, active: Iterable[Order], fills: Iterable[Fill]
    ) -> tuple[list[PrivateEvent], list[str]]:
        """(events, disappeared ids to query)."""
        events: list[PrivateEvent] = []
        current = {o.order_id: self._with_client_id(o) for o in active}
        for oid, o in current.items():
            if self._orders.get(oid) != o:
                events.append(o)
        fills = list(fills)
        new_fills = [f for f in fills if self._seen.add(f.trade_id)]
        ordered = sorted(new_fills, key=lambda f: f.ts)
        if self._seeded:
            events.extend(ordered)
        else:  # those from before startup: the account compares them with the journal (D-68)
            events.extend(PastFill(f) for f in ordered)
        events.extend(
            FeeUpdate(f.venue, f.trade_id, f.order_id, f.fee, f.fee_asset, f.ts)
            for f in fills
            if self._fees.get(f.trade_id, (f.fee, f.fee_asset)) != (f.fee, f.fee_asset)
        )
        self._fees = {f.trade_id: (f.fee, f.fee_asset) for f in fills}
        self._seeded = True
        tracked = self._sent | self._orders
        gone = [oid for oid in tracked if oid not in current]
        self._orders = current | {oid: tracked[oid] for oid in gone}
        self._sent = {}
        return events, gone

    def resolve(self, order: Order) -> list[PrivateEvent]:
        """Final state of a disappeared order; [] if not tracked."""
        if self._orders.pop(order.order_id, None) is None:
            return []
        return [self._with_client_id(order)]


@dataclass(frozen=True, slots=True)
class PrivateSource:
    """What each venue contributes to the refresh."""

    fetch_active: Callable[[], Awaitable[list[Order]]]
    fetch_fills: Callable[[], Awaitable[list[Fill]]]
    fetch_final: Callable[[str], Awaitable[Order | None]]  # None: not known yet


async def refresh(tracker: OrderTracker, src: PrivateSource) -> list[PrivateEvent]:
    events, gone = tracker.update(await src.fetch_active(), await src.fetch_fills())
    for oid in gone:
        final = await src.fetch_final(oid)
        if final is not None:
            events.extend(tracker.resolve(final))
    return events


async def poll_private(
    venue: VenueId,
    tracker: OrderTracker,
    src: PrivateSource,
    emit: Callable[[Any], None],
    *,
    every_s: Callable[[], float],
    source: Callable[[], Source],
    wake: asyncio.Event,
    clock: Callable[[], int],
) -> None:
    last: tuple[ConnStatus, Source] | None = None
    while True:
        wake.clear()
        start = time.perf_counter()
        try:
            events, status = await refresh(tracker, src), "up"
        except (httpx.HTTPError, VenueError):
            events, status = [], "down"
        latency = (time.perf_counter() - start) * 1000 if status == "up" else None
        now = (status, source())
        if now != last:
            last = now
            emit(ConnState(venue, "private", now[1], status, latency, since=clock()))
        for event in events:
            emit(event)
        try:
            await asyncio.wait_for(wake.wait(), every_s())
        except TimeoutError:
            pass


@dataclass(frozen=True, slots=True)
class _Crash:
    exc: BaseException


async def fan_in(
    *workers: Callable[[Callable[[Any], None]], Awaitable[None]],
) -> AsyncIterator[Any]:
    """A stream of what the workers emit. Their errors are re-raised here; closing the
    stream cancels them all."""
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def guarded(work: Awaitable[None]) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            queue.put_nowait(_Crash(error))

    tasks = [asyncio.create_task(guarded(w(queue.put_nowait))) for w in workers]
    try:
        while True:
            item = await queue.get()
            if isinstance(item, _Crash):
                raise item.exc
            yield item
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
