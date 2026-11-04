"""MarketHub: market streams -> book, candles, connection state.

For each venue:
1. Loads the market and each timeframe's candle history over REST. On failure it
   retries every `retry_s` and publishes the `(rest, down)` state.
2. Consumes `market_stream()`:
   - Book and deltas go to `L2Book`.
   - Trades go to the `CandleBuilder`s. Those before the load are dropped: they are
     in the history already and would count volume twice.
   - `ConnState`s are kept for the status bar.

With `book_check_s`, the hub regularly checks the top of the local book (best bid and
ask, price and quantity) against the venue's REST book (RNF-24, D-76): NonKYC's WS can
stay connected without sending changes. If they match, that moment counts as
freshness (`checked_at`); if not, the book is in doubt (`suspect`) until the next
snapshot or a matching check, a new book is asked from the adapter and `book_resync`
is written. A check during which a book message arrives does not count, and a failed
REST confirms nothing.

Changes are notified to subscribers as `(venue, what)`, with `what` ∈ market | book |
candles | conn. The UI decides when to redraw (throttle). Events the hub accepts
(book, undropped trades, connection) also pass as is to those asking, to forward them
over the socket (D-66). Each adapter repairs book gaps (D-10, D-11), so the hub does
not ask for snapshots.

Each venue runs apart under `supervised` (RNF-14, D-28): a bug in one is reported to
`faults`, marks its market down and is retried with backoff, without stopping the
other.

Spec: RF-01, RF-02, RF-03, RF-14, RNF-14, RNF-24 · design.md §6.1
Tasks: T-12, T-13, T-27, T-62, T-78
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from ..adapters.base import MarketData, MarketEvent, VenueError, describe
from ..adapters.streaming import now_ms
from ..core.book import BookRow, L2Book
from ..core.candles import CandleBuilder
from ..core.journal import Journal
from ..core.models import (
    BookDelta,
    BookSnapshot,
    ConnState,
    Level,
    MarketSpec,
    Timeframe,
    Trade,
    VenueId,
)
from .faults import Faults, supervised

log = logging.getLogger(__name__)

TIMEFRAMES: tuple[Timeframe, ...] = ("1m", "5m", "1h", "4h", "1d")
CHECK_DEPTH = 5  # levels asked of REST in the check: only the top counts
Listener = Callable[[VenueId, str], None]
EventListener = Callable[[VenueId, BookSnapshot | BookDelta | Trade | ConnState], None]


@dataclass
class VenueMarket:
    """Live market state of a venue."""

    id: VenueId
    book: L2Book
    candles: dict[Timeframe, CandleBuilder]
    spec: MarketSpec | None = None
    conn: ConnState | None = None
    ready: bool = False  # market and history loaded
    error: str | None = None
    loaded_at: int = 0  # earlier trades are already in the history
    conn_at: int | None = None  # when the last ConnState arrived (hub clock)
    last_trade: Trade | None = field(default=None)
    checked_at: int | None = None  # last REST check that matched (D-76)
    suspect: bool = False  # the last check did not match and there is no new book
    book_events: int = 0  # book messages applied: they detect the check race


class MarketHub:
    def __init__(
        self,
        venues: Mapping[VenueId, MarketData],
        *,
        history: int = 300,
        retry_s: float = 5.0,
        clock: Callable[[], int] = now_ms,
        faults: Faults | None = None,
        journal: Journal | None = None,
        book_check_s: float | None = None,
    ) -> None:
        self._venues = dict(venues)
        self._journal = journal
        self._book_check_s = book_check_s  # None: no REST check
        self.faults = faults or Faults(clock=clock)
        self._history = history
        self._retry_s = retry_s
        self._clock = clock
        self._listeners: list[Listener] = []
        self._event_listeners: list[EventListener] = []
        self.state: dict[VenueId, VenueMarket] = {
            vid: VenueMarket(vid, L2Book(vid), {tf: CandleBuilder(tf) for tf in TIMEFRAMES})
            for vid in self._venues
        }

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def subscribe_events(self, listener: EventListener) -> None:
        """Each market event the hub applies, after applying it."""
        self._event_listeners.append(listener)

    def _emit(self, venue: VenueId, event: BookSnapshot | BookDelta | Trade | ConnState) -> None:
        for listener in self._event_listeners:
            listener(venue, event)

    def _notify(self, venue: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(venue, what)

    async def run(self) -> None:
        """Keep all venues alive until cancelled."""
        await asyncio.gather(
            *(
                supervised(
                    lambda vid=vid, v=v: self._run_venue(vid, v),
                    lambda exc, vid=vid: self._crash(vid, exc),
                    retry_s=self._retry_s,
                )
                for vid, v in self._venues.items()
            )
        )

    def _crash(self, vid: VenueId, exc: Exception) -> None:
        fault = self.faults.report("market", exc, venue=vid)
        self.state[vid].error = fault.error
        self.apply(vid, ConnState(vid, "market", "ws", "down", None, self._clock()))

    async def aclose(self) -> None:
        """Close the HTTP clients of the venues having them."""
        for venue in self._venues.values():
            close = getattr(venue, "aclose", None)
            if close is not None:
                await close()

    @property
    def clock(self) -> Callable[[], int]:
        """Hub's local clock (epoch ms); the app shares it."""
        return self._clock

    def clock_skew_ms(self, vid: VenueId) -> int | None:
        """Venue clock skew against local, if the adapter measures it (RNF-16): test fakes
        and dry-run need not."""
        return getattr(self._venues[vid], "clock_skew_ms", None)

    def roll(self, now: int | None = None) -> None:
        """Open flat candles up to `now` even without trades."""
        now = self._clock() if now is None else now
        for vid, st in self.state.items():
            if st.ready:
                for builder in st.candles.values():
                    builder.roll(now)
                self._notify(vid, "candles")

    async def _run_venue(self, vid: VenueId, venue: MarketData) -> None:
        st = self.state[vid]
        await self._load(vid, venue, st)
        checks = asyncio.create_task(self._check_books(vid)) if self._book_check_s else None
        try:
            async with aclosing(venue.market_stream()) as stream:
                async for event in stream:
                    self.apply(vid, event)
        finally:
            if checks is not None:
                checks.cancel()
                await asyncio.gather(checks, return_exceptions=True)

    async def _check_books(self, vid: VenueId) -> None:
        assert self._book_check_s is not None
        while True:
            await asyncio.sleep(self._book_check_s)
            try:
                await self.check_book(vid)
            except Exception as exc:  # a bug here cannot stop the market
                self.faults.report("book-check", exc, venue=vid)

    async def check_book(self, vid: VenueId) -> bool | None:
        """Check the top of the local book against REST (RNF-24, D-76). True if they match,
        False if not (and asks for a new book), None without a check: no book, failed REST
        or a book message while waiting."""
        st = self.state[vid]
        if not (st.ready and st.book.valid):
            return None
        seen = st.book_events
        try:
            rest = await self._venues[vid].book_snapshot(CHECK_DEPTH)
        except (VenueError, httpx.HTTPError) as exc:
            log.debug("%s: book check without REST: %s", vid, describe(exc))
            return None
        if st.book_events != seen or not st.book.valid:
            return None
        local = _top(*st.book.top(1))
        # L2Book deletes qty 0 levels: REST is read the same way
        remote = _top(*((lv for lv in side if lv.qty > 0) for side in (rest.bids, rest.asks)))
        if local == remote:
            st.checked_at, st.suspect = self._clock(), False
            return True
        st.suspect = True
        log.warning("%s: local book (%s) differs from REST (%s)", vid, _text(local), _text(remote))
        if self._journal is not None:
            self._journal.write(
                "book_resync", venue=vid, data={"local": _sides(local), "rest": _sides(remote)}
            )
        resync = getattr(self._venues[vid], "resync", None)
        if resync is not None:
            await resync()
        self._notify(vid, "book")
        return False

    async def _load(self, vid: VenueId, venue: MarketData, st: VenueMarket) -> None:
        while True:
            try:
                spec = await venue.market()
                history = {tf: await venue.candles(tf, self._history) for tf in TIMEFRAMES}
                for tf, candles in history.items():
                    st.candles[tf].load(candles)  # ValueError if misaligned
            except (VenueError, httpx.HTTPError, ValueError) as exc:
                st.error = describe(exc)
                log.warning("%s: could not load the market: %s", vid, st.error)
                self.apply(vid, ConnState(vid, "market", "rest", "down", None, self._clock()))
                await asyncio.sleep(self._retry_s)
                continue
            st.spec = spec
            st.loaded_at, st.ready, st.error = self._clock(), True, None
            log.info("%s: market loaded", vid)
            self._notify(vid, "market")
            self._notify(vid, "candles")
            return

    def apply(self, vid: VenueId, event: MarketEvent) -> None:
        st = self.state[vid]
        if isinstance(event, BookSnapshot | BookDelta):
            st.book.apply(event, received_at=self._clock())
            st.book_events += 1
            if isinstance(event, BookSnapshot):
                st.suspect = False  # a delta on a bad book does not fix it
            self._emit(vid, event)
            self._notify(vid, "book")
        elif isinstance(event, Trade):
            if not st.ready or event.ts < st.loaded_at:
                return
            st.last_trade = event
            for builder in st.candles.values():
                builder.on_trade(event)
            self._emit(vid, event)
            self._notify(vid, "candles")
        elif isinstance(event, ConnState):
            st.conn, st.conn_at = event, self._clock()
            self._emit(vid, event)
            self._notify(vid, "conn")


Top = tuple[tuple[Decimal, Decimal] | None, tuple[Decimal, Decimal] | None]


def _level(lv: BookRow | Level | None) -> tuple[Decimal, Decimal] | None:
    return (lv.price, lv.qty) if lv is not None else None


def _top(bids: Iterable[BookRow | Level], asks: Iterable[BookRow | Level]) -> Top:
    """(price, quantity) of the best bid and ask; None if the side is missing."""
    best_bid = max(bids, key=lambda lv: lv.price, default=None)
    best_ask = min(asks, key=lambda lv: lv.price, default=None)
    return _level(best_bid), _level(best_ask)


def _sides(top: Top) -> dict[str, list[Decimal] | None]:
    bid, ask = top
    return {"bid": list(bid) if bid else None, "ask": list(ask) if ask else None}


def _text(top: Top) -> str:
    return ", ".join(
        f"{name} {lv[0]:f} × {lv[1]:f}" if lv else f"sin {name}"
        for name, lv in zip(("bid", "ask"), top, strict=True)
    )
