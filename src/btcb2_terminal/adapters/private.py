"""Seguimiento de órdenes y fills propios, común a los dos venues (D-14).

Cada refresco pide por REST las órdenes activas y los últimos trades propios, y
`OrderTracker` saca la diferencia con el anterior:

- Una orden nueva o que cambia (cantidad llena, estado) sale como `Order`.
- Un trade propio nuevo sale como `Fill`. Los del primer refresco son historia
  y no se avisan.
- Un trade ya visto que vuelve con otra fee u otro activo de fee sale como
  `FeeUpdate` (NonKYC rehace la fee en NKYC, D-64).
- Una orden que deja de estar activa se consulta aparte hasta saber cómo acabó
  (llena o cancelada); mientras tanto sigue pendiente.

`poll_private` repite el refresco cada `every_s()` o antes si alguien pone
`wake` (el WS privado de NonKYC cuando llega un reporte), y emite un `ConnState`
del canal privado cada vez que cambia el estado o la fuente. `fan_in` junta
varios trabajadores en un solo stream y relanza sus bugs en el consumidor.

Spec: RF-08, RF-09, RF-11, RF-14, RNF-04 · design.md §4, §6.5 · Tareas: T-17, T-59
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
        self._orders: dict[str, Order] = {}  # activas, y las desaparecidas sin resolver
        self._sent: dict[str, Order] = {}  # enviadas por la terminal, aún sin ver (D-41)
        self._client_ids: dict[str, str] = {}
        self._seen = SeenIds()
        self._fees: dict[str, tuple[Decimal, str | None]] = {}  # del último refresco
        self._seeded = False

    @property
    def open(self) -> list[Order]:
        sent = [o for oid, o in self._sent.items() if oid not in self._orders]
        return [o for o in [*self._orders.values(), *sent] if not o.is_done]

    def remember(self, order: Order) -> None:
        """Una orden recién enviada por la terminal: guarda su client_id y, si sigue
        viva, la sigue desde ya. Si se llena antes del siguiente refresco, no saldrá
        nunca como activa; así sale como desaparecida y se consulta (D-41). Si sale
        activa, se avisa como cualquier orden nueva: la cuenta la necesita."""
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
        """(eventos, ids desaparecidos que hay que consultar)."""
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
        else:  # los de antes de arrancar: la cuenta los compara con el journal (D-68)
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
        """Estado final de una orden desaparecida; [] si no se seguía."""
        if self._orders.pop(order.order_id, None) is None:
            return []
        return [self._with_client_id(order)]


@dataclass(frozen=True, slots=True)
class PrivateSource:
    """Lo que cada venue aporta al refresco."""

    fetch_active: Callable[[], Awaitable[list[Order]]]
    fetch_fills: Callable[[], Awaitable[list[Fill]]]
    fetch_final: Callable[[str], Awaitable[Order | None]]  # None: aún no se sabe


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
    """Un stream con lo que emiten los trabajadores. Un error suyo se relanza
    aquí; al cerrar el stream se cancelan todos."""
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def guarded(work: Awaitable[None]) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            queue.put_nowait(_Crash(exc))

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
