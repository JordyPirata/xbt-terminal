"""Async sliding window limiter with priority.

One limiter per venue, shared by *all* its REST requests: that way Neoxa's polling
and orders spend the same budget (RNF-03).

Each request declares its kind, which sets the windows it uses and its priority:

    kind      windows             priority
    cancel    general + cancel    0 (highest)
    order     general + order     1
    private   general             2  (polling of orders, trades, balances)
    market    general             3  (book, candles, REST fallback)

When room frees up, the highest priority wait goes first, and within a priority
in order of arrival. An order held only by its own window (8 orders/10 s) does not
slow polling, which does not use that window.

Spec: RNF-03 · design.md §5 · Task: T-07
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

PRIORITY = {"cancel": 0, "order": 1, "private": 2, "market": 3}


@dataclass
class _Window:
    limit: int
    span: float
    stamps: deque[float] = field(default_factory=deque)

    def wait(self, now: float) -> float:
        """Seconds until there is room; 0 if there already is."""
        while self.stamps and now - self.stamps[0] >= self.span:
            self.stamps.popleft()
        if len(self.stamps) < self.limit:
            return 0.0
        return self.stamps[0] + self.span - now


class RateLimiter:
    def __init__(
        self,
        general: tuple[int, float],
        order: tuple[int, float] | None = None,
        cancel: tuple[int, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._windows = {"general": _Window(*general)}
        if order:
            self._windows["order"] = _Window(*order)
        if cancel:
            self._windows["cancel"] = _Window(*cancel)
        self._clock = clock
        self._queue: list[tuple[int, int, str, asyncio.Future[None]]] = []
        self._seq = itertools.count()
        self._timer: asyncio.TimerHandle | None = None

    async def acquire(self, kind: str) -> None:
        """Wait until a request of this kind can go, and count it."""
        if kind not in PRIORITY:
            raise ValueError(f"tipo de petición desconocido: {kind!r}")
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._queue, (PRIORITY[kind], next(self._seq), kind, future))
        self.dispatch()
        await future

    def dispatch(self) -> None:
        """Serve the waits that fit and schedule the next check."""
        now = self._clock()
        waiting: list[tuple[int, int, str, asyncio.Future[None]]] = []
        next_wake: float | None = None

        while self._queue:
            item = heapq.heappop(self._queue)
            _, _, kind, future = item
            if future.done():  # the waiting task was cancelled
                continue
            windows = self._windows_for(kind)
            wait = max(window.wait(now) for window in windows)
            if wait == 0:
                for window in windows:
                    window.stamps.append(now)
                future.set_result(None)
                continue
            waiting.append(item)
            next_wake = wait if next_wake is None else min(next_wake, wait)

        for item in waiting:
            heapq.heappush(self._queue, item)
        self._schedule(next_wake if waiting else None)

    def close(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _windows_for(self, kind: str) -> list[_Window]:
        own = self._windows.get(kind)
        return [self._windows["general"], own] if own else [self._windows["general"]]

    def _schedule(self, delay: float | None) -> None:
        self.close()
        if delay is not None:
            self._timer = asyncio.get_running_loop().call_later(delay, self.dispatch)
