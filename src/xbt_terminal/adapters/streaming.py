"""Common base of adapters with public WS and REST fallback, and connection supervisor.

Each venue implements `book_snapshot`, `_fetch_trades` (REST) and `_ws_events` (one
WS session opened with `ws_connect`: delivers data and raises when it drops). This
base adds the rest:

- `market_stream` and its supervisor (D-11):
  - Reconnection with exponential backoff and jitter.
  - From the third failure in a row, REST polling alongside until the WS is back.
  - Emits `ConnState` and re-emits the state every `latency_every_s` with the
    latency (moving average of the WS ping RTT or the REST requests).
- Trade deduplication by id across REST, the initial WS batch and live trades, so
  a reconnect does not repeat delivered trades.
- `parsing`: a missing or mismatched field is an API change → VenueError.
- `sending`: a send not known to have been applied → UncertainError (RNF-12, D-26).
- `timed`: each REST response with a `Date` header updates `clock_skew_ms`, the
  venue clock's skew against the local one (RNF-16, D-30).

Spec: RF-01, RF-14, CA-06, RNF-03, RNF-09, RNF-12, RNF-16, RNF-24 · design.md §4, §6.1
Tasks: T-08, T-09, T-12, T-25, T-29, T-78
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx
import websockets

from ..core.models import BookSnapshot, ConnState, ConnStatus, Source, Trade, VenueId
from .base import MarketEvent, UncertainError, VenueError, describe

log = logging.getLogger(__name__)

T = TypeVar("T")

TRADES_EVERY_POLLS = 5  # REST fallback: trades every 5 book rounds (D-09)
FALLBACK_TRADES = 100  # trades per fallback round: plenty for 5 s
SEEN_TRADES = 1000
WS_ERRORS = (OSError, TimeoutError, websockets.WebSocketException, VenueError)
# On close, wait no longer than this for the venue's reply: with the receive queue
# full (16 unread frames) a clean close would take the default 10 s.
WS_CLOSE_TIMEOUT_S = 1.0
FALLBACK_AFTER_FAILURES = 3  # design.md §6.1
# The request never left: a send failing like this created nothing (D-26)
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
PARSE_ERRORS = (KeyError, TypeError, ValueError, ArithmeticError)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def skew_ms(date: str | None, sent_ms: int, received_ms: int) -> int | None:
    """Venue time minus local, in ms, from the `Date` header of a response requested at
    `sent_ms` and received at `received_ms` (local time). `Date` is truncated to the
    second: its midpoint is taken, so the error is ±0.5 s plus half the RTT. None if
    there is no header or it is unreadable."""
    if not date:
        return None
    try:
        server = parsedate_to_datetime(date)
    except (TypeError, ValueError):
        return None
    server_ms = int(server.timestamp() * 1000) + 500
    return server_ms - (sent_ms + received_ms) // 2


def iso_ms(text: str) -> int:
    """'2026-10-20T14:23:36.539Z' → epoch ms, without going through float."""
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"fecha sin zona horaria: {text!r}")
    return (dt - _EPOCH) // _MS


class SeenIds:
    """Ids of trades already delivered, bounded to the last `size`."""

    def __init__(self, size: int = SEEN_TRADES) -> None:
        self._order: deque[str] = deque()
        self._ids: set[str] = set()
        self._size = size

    def add(self, trade_id: str) -> bool:
        """True if new."""
        if trade_id in self._ids:
            return False
        self._order.append(trade_id)
        self._ids.add(trade_id)
        if len(self._order) > self._size:
            self._ids.discard(self._order.popleft())
        return True


class Backoff:
    """Wait before retry n (n ≥ 1): `min(max, base × 2^(n-1))` with ±20 % jitter, never
    past `max`."""

    def __init__(
        self, base_s: float, max_s: float, rng: Callable[[], float] = random.random
    ) -> None:
        self.base_s, self.max_s, self._rng = base_s, max_s, rng

    def delay(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError(f"reintento inválido: {attempt}")
        raw = min(self.max_s, self.base_s * 2 ** (attempt - 1))
        return min(self.max_s, raw * (0.8 + 0.4 * self._rng()))


class LatencyMeter:
    """Exponential moving average in ms. Samples ≤ 0 are ignored (websockets gives 0.0
    until the first ping)."""

    def __init__(self, alpha: float = 0.3) -> None:
        self._alpha = alpha
        self.ms: float | None = None

    def add(self, ms: float) -> None:
        if ms <= 0:
            return
        self.ms = ms if self.ms is None else self.ms + self._alpha * (ms - self.ms)


@dataclass(frozen=True, slots=True)
class _Crash:
    """Non-network error (a bug): re-raised in the consumer."""

    exc: BaseException


class StreamingVenue:
    id: VenueId
    name: str
    max_trades: int  # trade limit per REST request of the venue

    def __init__(
        self,
        *,
        ws_url: str,
        book_depth: int,
        fallback_every_s: float = 1.0,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 30.0,
        fallback_after: int = FALLBACK_AFTER_FAILURES,
        ping_interval_s: float = 5.0,
        latency_every_s: float = 5.0,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._ws_url = ws_url
        self._book_depth = book_depth
        self._fallback_every_s = fallback_every_s
        self._backoff = Backoff(backoff_base_s, backoff_max_s, rng)
        self._fallback_after = fallback_after
        self._ping_interval_s = ping_interval_s
        self._latency_every_s = latency_every_s
        self._clock = clock
        self._seen = SeenIds()
        self._ws: Any = None  # open connection, to read its latency
        self._ws_latency = LatencyMeter()
        self._rest_latency = LatencyMeter()
        self._current: ConnState | None = None
        self.clock_skew_ms: int | None = None  # venue - local, from the last `Date` (RNF-16)

    # -- to implement by each venue -----------------------------------------

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        raise NotImplementedError

    async def _fetch_trades(self, limit: int) -> list[tuple[str, Trade]]:
        """(id, Trade) of the most recent REST trades, in any order."""
        raise NotImplementedError

    def _ws_events(self) -> AsyncIterator[MarketEvent]:
        """One WS session: data (book, deltas, trades); raises or ends when it drops."""
        raise NotImplementedError

    async def resync(self) -> None:
        """Ask for a new book because the local one does not match REST (D-76). By default
        closes the WS: the supervisor reconnects and the new session starts with a
        snapshot. Without an open WS (REST fallback), each round already is one."""
        ws = self._ws
        if ws is not None:
            log.warning("%s: book differs from REST; reconnecting the WS", self.name)
            await ws.close()

    # -- helpers for the venues --------------------------------------------

    @asynccontextmanager
    async def ws_connect(self) -> AsyncIterator[Any]:
        async with websockets.connect(
            self._ws_url,
            open_timeout=10,
            close_timeout=WS_CLOSE_TIMEOUT_S,
            ping_interval=self._ping_interval_s,
        ) as ws:
            self._ws = ws
            await ws.ping()  # first latency measure without waiting for the keepalive
            try:
                yield ws
            finally:
                self._ws = None

    async def timed(self, request: Awaitable[T]) -> T:
        """Await a REST request and note its duration as latency and, with a `Date` header,
        the clock skew with the venue."""
        start, sent = time.perf_counter(), self._clock()
        result = await request
        self._rest_latency.add((time.perf_counter() - start) * 1000)
        if isinstance(result, httpx.Response):
            skew = skew_ms(result.headers.get("date"), sent, self._clock())
            if skew is not None:
                self.clock_skew_ms = skew
        return result

    def parsing(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except VenueError:
            raise
        except PARSE_ERRORS as error:
            raise VenueError(f"{self.name}: respuesta inesperada ({error!r})") from error

    async def sending(self, request: Awaitable[Any], parse: Callable[[Any], T]) -> T:
        """Await a send that creates something on the venue and read the reply with `parse`.
        If unknown whether the venue applied it, UncertainError (D-26): network failure
        after connecting, HTTP 5xx, a 2xx that is not JSON or an unreadable response.
        Without connecting, a 4xx or an explicit venue error, the error passes as is."""
        try:
            payload = await request
        except NOT_SENT:
            raise
        except httpx.HTTPError as error:
            detail = f"{type(error).__name__} {error}".strip()
            raise UncertainError(f"{self.name}: sin respuesta clara ({detail})") from error
        except UncertainError:
            raise
        except VenueError as error:
            status = error.status
            if status is not None and (status >= 500 or (error.unreadable and status < 400)):
                raise UncertainError(str(error), status=status) from error
            raise
        try:
            return parse(payload)
        except (*PARSE_ERRORS, VenueError) as error:
            raise UncertainError(f"{self.name}: respuesta al envío ilegible ({error})") from error

    async def recent_trades(self, limit: int) -> list[Trade]:
        """Most recent trades in chronological order. Marks them as seen."""
        trades = await self._fetch_trades(min(limit, self.max_trades))
        for trade_id, _ in trades:
            self._seen.add(trade_id)
        return sorted((trade for _, trade in trades), key=lambda trade: trade.ts)

    def fresh(self, trades: list[tuple[str, Trade]]) -> list[Trade]:
        """Only those not delivered yet, in chronological order."""
        return sorted(
            (trade for i, trade in trades if self._seen.add(i)), key=lambda trade: trade.ts
        )

    # -- state and latency -----------------------------------------------------

    def _latency_ms(self, source: Source) -> float | None:
        if source == "ws":
            if self._ws is not None:
                self._ws_latency.add(self._ws.latency * 1000)
            return self._ws_latency.ms
        return self._rest_latency.ms

    def state(self, source: Source, status: ConnStatus) -> ConnState:
        latency = self._latency_ms(source) if status == "up" else None
        return ConnState(self.id, "market", source, status, latency, since=self._clock())

    # -- supervisor --------------------------------------------------------------

    async def market_stream(self) -> AsyncIterator[MarketEvent]:
        """Market events from whichever source is alive, with `ConnState` on state changes
        and as a heartbeat. A bug (not a network failure) is re-raised."""
        queue: asyncio.Queue[MarketEvent | _Crash] = asyncio.Queue()
        self._current = None

        def emit(event: MarketEvent) -> None:
            if isinstance(event, ConnState):
                self._current = event
            queue.put_nowait(event)

        async def guarded(work: Awaitable[None]) -> None:
            try:
                await work
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                queue.put_nowait(_Crash(error))

        def spawn(work: Awaitable[None]) -> asyncio.Task[None]:
            return asyncio.create_task(guarded(work))

        tasks = [spawn(self._supervise(emit, spawn)), spawn(self._heartbeat(emit))]
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

    async def _supervise(
        self,
        emit: Callable[[MarketEvent], None],
        spawn: Callable[[Awaitable[None]], asyncio.Task[None]],
    ) -> None:
        failures = 0
        poller: asyncio.Task[None] | None = None
        try:
            while True:
                got_book = False
                cause = "sesión cerrada"
                try:
                    async with aclosing(self._ws_events()) as events:
                        async for event in events:
                            if not got_book and isinstance(event, BookSnapshot):
                                got_book, failures = True, 0
                                log.info("%s: WS connected", self.name)
                                if poller is not None:
                                    await _stop(poller)
                                    poller = None
                                    log.info("%s: end of fallback REST polling", self.name)
                                emit(self.state("ws", "up"))
                            emit(event)
                except WS_ERRORS as error:
                    cause = f"{type(error).__name__}: {error}"
                failures += 1
                delay = self._backoff.delay(failures)
                log.warning(
                    "%s: WS down (%s); retrying in %.1f s", self.name, cause, delay
                )  # fmt: skip
                if poller is None:
                    emit(self.state("ws", "reconnecting"))
                    if failures >= self._fallback_after:
                        log.warning(
                            "%s: fallback REST polling after %d WS failures",
                            self.name, failures,
                        )  # fmt: skip
                        poller = spawn(self._poll(emit))
                await asyncio.sleep(delay)
        finally:
            if poller is not None:
                await _stop(poller)

    async def _poll(self, emit: Callable[[MarketEvent], None]) -> None:
        """Fallback REST polling until the supervisor cancels it."""
        status: ConnStatus | None = None
        poll_count = 0
        while True:
            try:
                events: list[MarketEvent] = [await self.book_snapshot(self._book_depth)]
                if poll_count % TRADES_EVERY_POLLS == 0:
                    limit = min(FALLBACK_TRADES, self.max_trades)
                    events += self.fresh(await self._fetch_trades(limit))
                new_status: ConnStatus = "up"
            except (httpx.HTTPError, VenueError) as error:
                events, new_status = [], "down"
                if status != "down":
                    log.warning("%s: fallback REST down: %s", self.name, describe(error))
            if new_status != status:
                if new_status == "up":
                    log.info("%s: fallback REST has data", self.name)
                status = new_status
                emit(self.state("rest", status))
            for event in events:
                emit(event)
            poll_count += 1
            await asyncio.sleep(self._fallback_every_s)

    async def _heartbeat(self, emit: Callable[[MarketEvent], None]) -> None:
        """Re-emit the live state with the current latency; reconnecting, nothing."""
        while True:
            await asyncio.sleep(self._latency_every_s)
            current = self._current
            if current is not None and current.status == "up":
                emit(replace(current, latency_ms=self._latency_ms(current.source)))


async def _stop(task: asyncio.Task[Any]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
