"""Replay of a recording (RF-34, RF-35, D-80): simulated venues under the usual `Engine`.

- `ReplayVenue` fulfils `MarketData`: emits the events with their relative times.
- `SimVenue` trades against a `ReplayVenue`'s book: the order arrives after
  `latency_ms`, matches as taker (`core.replay.match`) and the rest stays open.
- `VirtualLoop` is an asyncio loop with no waiting: the clock jumps to the next timer.
  With it, the report takes seconds and always comes out the same.

Bot, RiskCheck, journal and results do not know this is a replay.

Spec: RF-34, RF-35, CA-19, CA-20 · design.md §6.14 · Task: T-87
"""

from __future__ import annotations

import asyncio
import random
import selectors
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..adapters.base import MarketEvent
from ..adapters.dryrun import DryRunVenue
from ..adapters.streaming import now_ms
from ..config import ReplayConfig
from ..core.book import L2Book
from ..core.bot import BotLimits, BotState, Fire, VenueView, decide
from ..core.models import (
    ZERO,
    Balance,
    BookDelta,
    BookSnapshot,
    Candle,
    ConnState,
    Level,
    MarketSpec,
    Order,
    OrderRequest,
    Side,
    Timeframe,
    Trade,
    VenueId,
)
from ..core.replay import HANGING, ReplayReport, match, summarize
from ..core.results import compute, read_rows
from .engine import Engine
from .market import MarketHub
from .recording import RecordingInfo, read_recording

MATCH_DEPTH = 200  # levels per side an order matches against
PROBE_DEPTH = 50  # the same the bot looks at
PLENTY = Decimal(10**9)  # balance to spare for counting crossings
TAIL_S = 30.0  # after the recording, so the last double closes
CLOCK_RESOLUTION_S = 1e-6
_ARMED = BotState(status="armed")


class Timeline:
    """Time origin shared by the venues of a replay."""

    def __init__(self, info: RecordingInfo, clock: Callable[[], int], *, venues: int) -> None:
        self._info = info
        self._clock = clock
        self._pending = venues
        self._offset: int | None = None
        self.done = asyncio.Event()

    @property
    def offset(self) -> int:
        """Now minus the first event; fixed the first time it is asked."""
        if self._offset is None:
            self._offset = self._clock() - self._info.first
        return self._offset

    @property
    def last(self) -> int:
        return self._info.last

    def finish(self) -> None:
        self._pending -= 1
        if self._pending == 0:
            self.done.set()


def _shifted(event: Any, offset: int) -> Any:
    """The event in replay time: the hub drops trades "from before loading", and the
    screen draws candles with these times."""
    if isinstance(event, ConnState):
        return replace(event, since=event.since + offset)
    if isinstance(event, BookSnapshot | BookDelta | Trade):
        return replace(event, ts=event.ts + offset)
    return event


class ReplayVenue:
    """`MarketData` reading a recording; no network."""

    def __init__(
        self,
        venue: VenueId,
        path: Path,
        info: RecordingInfo,
        timeline: Timeline,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.id = venue
        self._path = path
        self.spec = info.specs[venue]
        self.timeline = timeline
        self._clock = clock
        self.book = L2Book(venue)
        self.on_book: Callable[[], None] = lambda: None  # used by `SimVenue`

    def apply(self, event: BookSnapshot | BookDelta) -> None:
        self.book.apply(event, received_at=self._clock())
        self.on_book()

    async def _until(self, at: int) -> None:
        delay = (at - self._clock()) / 1000
        if delay > 0:
            await asyncio.sleep(delay)

    async def market(self) -> MarketSpec:
        return self.spec

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        """The replayed book: the RNF-24 check always matches."""
        return self.book.snapshot(depth) or BookSnapshot(self.id, (), (), self._clock())

    async def recent_trades(self, limit: int) -> list[Trade]:
        return []

    async def candles(self, tf: Timeframe, limit: int) -> list[Candle]:
        return []

    async def market_stream(self) -> AsyncIterator[MarketEvent]:
        offset = self.timeline.offset
        for row in read_recording(self._path):
            if row.venue != self.id or isinstance(row.event, MarketSpec):
                continue
            await self._until(row.t + offset)
            event = _shifted(row.event, offset)
            if isinstance(event, BookSnapshot | BookDelta):
                self.apply(event)
            yield event
        await self._until(self.timeline.last + offset)  # one venue does not end before the other
        self.timeline.finish()
        yield ConnState(self.id, "market", "ws", "down", None, self._clock())
        await asyncio.Event().wait()  # no more market, until the daemon is stopped


class SimVenue(DryRunVenue):
    """Orders against the recording's book, with a competitor taking whole levels. The
    recorded book does not change with what matched, but that does not match again: it
    stays spent until the recording moves that level (D-80)."""

    def __init__(
        self, feed: ReplayVenue, config: ReplayConfig, *, clock: Callable[[], int] = now_ms
    ) -> None:
        spec = feed.spec
        balances = {
            spec.base: Balance(spec.base, config.base_balance),
            spec.quote: Balance(spec.quote, config.quote_balance),
        }
        super().__init__(feed, balances, spec=lambda: spec, clock=clock)
        self._feed = feed
        feed.on_book = self._on_book
        self._latency_s = config.latency_ms / 1000
        self._competitor_pct = config.competitor_pct
        self._rng = random.Random(f"{config.seed}-{feed.id}")
        # (side, price) → (recorded level quantity, spent of it)
        self._spent: dict[tuple[Side, Decimal], tuple[Decimal, Decimal]] = {}

    def _taken(self, level: object) -> bool:
        return Decimal(self._rng.randrange(10_000)) / 100 < self._competitor_pct

    def _on_book(self) -> None:
        """The recording moved the book: what was spent of a level that changed or left stops
        counting."""
        book = self._feed.book.snapshot(MATCH_DEPTH)
        if not self._spent or book is None:
            return
        now = {("buy", price_level.price): price_level.qty for price_level in book.asks}
        now |= {("sell", price_level.price): price_level.qty for price_level in book.bids}
        self._spent = {key: spent for key, spent in self._spent.items() if now.get(key) == spent[0]}

    def _unspent(self, side: Side, levels: tuple[Level, ...]) -> list[Level]:
        """The levels with what they have left."""
        out = []
        for level in levels:
            left = level.qty - self._spent.get((side, level.price), (ZERO, ZERO))[1]
            if left > 0:
                out.append(replace(level, qty=left))
        return out

    def _spend(self, side: Side, levels: tuple[Level, ...], price: Decimal, qty: Decimal) -> None:
        recorded = next(price_level.qty for price_level in levels if price_level.price == price)
        used = self._spent.get((side, price), (recorded, ZERO))[1]
        self._spent[side, price] = (recorded, used + qty)

    async def place_limit(self, order_request: OrderRequest) -> Order:
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        order = await super().place_limit(order_request)
        book = self._feed.book.snapshot(MATCH_DEPTH)
        if book is None:
            return order
        levels = book.asks if order_request.side == "buy" else book.bids
        left = self._unspent(order_request.side, levels)
        for price, qty in match(
            order_request.side, order_request.price, order_request.qty, left, self._taken
        ):
            self._spend(order_request.side, levels, price, qty)
            order = await self.simulate_fill(order.order_id, qty, price=price)
        return order


class _VirtualSelector(selectors.DefaultSelector):
    """Instead of waiting, advance the loop clock to the next timer."""

    loop: VirtualLoop

    def select(self, timeout: float | None = None) -> list[Any]:
        ready = super().select(0)
        if ready or timeout is None or timeout <= 0:
            if not ready and timeout is None:
                raise RuntimeError("tiempo virtual: nada que esperar")
            return ready
        self.loop.advance(timeout)
        return ready


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self, start_s: float) -> None:
        selector = _VirtualSelector()
        selector.loop = self
        self._now = start_s
        super().__init__(selector)
        # with epoch time, adding 1 ns does not move a float: a timer due right now would
        # never fire
        self._clock_resolution = CLOCK_RESOLUTION_S

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def now_ms(self) -> int:
        return round(self._now * 1000)


class CrossingProbe:
    """Count crossings over the threshold: each run in which the bot's decision would
    fire with balance to spare, without cooldown or caps (RF-35)."""

    def __init__(self, hub: MarketHub, limits: BotLimits, clock: Callable[[], int]) -> None:
        self._hub = hub
        self._limits = replace(limits, max_move_pct=None, max_daily_btc=PLENTY)
        self._clock = clock
        self._open = False
        self.count = 0
        hub.subscribe(self._on_change)

    def _on_change(self, venue: VenueId, what: str) -> None:
        if what != "book":
            return
        views: dict[VenueId, VenueView] = {}
        for venue_id, state in self._hub.state.items():
            book = state.book.snapshot(PROBE_DEPTH)
            if state.spec is None or book is None:
                return
            views[venue_id] = VenueView(state.spec, book.bids, book.asks, PLENTY, PLENTY)
        crossed = isinstance(decide(views, _ARMED, self._limits, self._clock()), Fire)
        self.count += crossed and not self._open
        self._open = crossed


async def run_report(engine: Engine, timeline: Timeline, *, tail_s: float = TAIL_S) -> None:
    """The engine's services until the recording ends, with the bot armed. A hanging leg
    re-arms it: this measures, it does not trade (RF-35)."""
    bot = engine.bot
    assert bot is not None
    loop = asyncio.get_running_loop()

    def rearm(state: BotState) -> None:
        if state.status == "halted" and (state.reason or "").startswith(HANGING):
            loop.call_soon(bot.arm)

    bot.subscribe(rearm)
    tasks = [asyncio.create_task(work()) for _, work in engine.services() + engine.watchers()]
    try:
        bot.arm()
        await timeline.done.wait()
        await asyncio.sleep(tail_s)
        bot.disarm()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.aclose()


def summarize_journal(directory: Path, crossings: int, info: RecordingInfo) -> ReplayReport:
    lines: list[str] = []
    for path in sorted(directory.glob("journal-*.jsonl")):
        lines += path.read_text(encoding="utf-8").splitlines()
    rows, _ = read_rows(lines)
    results = compute(rows, mode="dry-run")
    filled = sum(d.result is not None and d.mismatch == 0 for d in results.doubles)
    return summarize(
        rows,
        crossings=crossings,
        filled=filled,
        net=results.summary.total,
        duration_ms=info.last - info.first,
    )
