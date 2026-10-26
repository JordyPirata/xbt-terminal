"""Limitador async de ventanas deslizantes con prioridad.

Un limitador por venue, compartido por *todas* sus peticiones REST: así el
polling y las órdenes de Neoxa gastan el mismo presupuesto (RNF-03).

Cada petición declara su tipo, que fija qué ventanas consume y su prioridad:

    tipo      ventanas            prioridad
    cancel    general + cancel    0 (la más alta)
    order     general + order     1
    private   general             2  (polling de órdenes, trades, balances)
    market    general             3  (book, velas, respaldo REST)

Cuando se libera hueco se atiende primero la espera de más prioridad, y dentro
de la misma prioridad por orden de llegada. Una orden bloqueada solo por su
ventana propia (8 órdenes/10 s) no frena al polling, que no usa esa ventana.

Spec: RNF-03 · design.md §5 · Tarea: T-07
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
        """Segundos hasta que haya hueco; 0 si ya lo hay."""
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
        """Espera hasta poder hacer una petición del tipo dado y la contabiliza."""
        if kind not in PRIORITY:
            raise ValueError(f"tipo de petición desconocido: {kind!r}")
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._queue, (PRIORITY[kind], next(self._seq), kind, future))
        self.dispatch()
        await future

    def dispatch(self) -> None:
        """Atiende las esperas que ya caben y programa la siguiente revisión."""
        now = self._clock()
        waiting: list[tuple[int, int, str, asyncio.Future[None]]] = []
        next_wake: float | None = None

        while self._queue:
            item = heapq.heappop(self._queue)
            _, _, kind, future = item
            if future.done():  # la tarea que esperaba se canceló
                continue
            windows = self._windows_for(kind)
            wait = max(w.wait(now) for w in windows)
            if wait == 0:
                for w in windows:
                    w.stamps.append(now)
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
