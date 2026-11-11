"""StreamingVenue connection supervisor (T-12, D-11) with a network-less fake venue: each
WS session follows a script (fail, deliver data, drop or stay)."""

import asyncio
from types import SimpleNamespace

import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.adapters.streaming import Backoff, LatencyMeter, StreamingVenue
from xbt_terminal.core.models import BookSnapshot, ConnState, D, Level, Trade

WS_BOOK = BookSnapshot("nk", (Level(D("0.0072"), D("1")),), (Level(D("0.0073"), D("1")),), 1, 7)
REST_BOOK = BookSnapshot("nk", (Level(D("0.0071"), D("1")),), (Level(D("0.0074"), D("1")),), 2)
TRADE = Trade("nk", D("0.0072"), D("0.5"), "buy", 3)


class FakeVenue(StreamingVenue):
    """Scripts per session: "fail" (does not connect), "hang" (connects and never gives
    data) or a list of events, where "drop" cuts the session and "wait" pauses it."""

    id = "nk"
    name = "Fake"
    max_trades = 10

    def __init__(self, *sessions, rest_ok=True, ws_latency=None, **kw):
        opts = dict(
            book_depth=5,
            fallback_every_s=0.01,
            backoff_base_s=0.01,
            backoff_max_s=0.08,
            rng=lambda: 0.5,  # no jitter: factor 1.0
            latency_every_s=60.0,
            clock=lambda: 1_000,
        )
        super().__init__(ws_url="ws://no-se-usa", **(opts | kw))
        self.sessions = list(sessions)
        self.rest_ok = rest_ok
        self.ws_latency = ws_latency
        self.rest_calls = 0
        self.delays: list[float] = []
        delay = self._backoff.delay
        self._backoff.delay = lambda attempt: self.delays.append(delay(attempt)) or delay(attempt)

    async def book_snapshot(self, depth):
        self.rest_calls += 1
        await self.timed(asyncio.sleep(0.005))
        if not self.rest_ok:
            raise VenueError("REST caído")
        return REST_BOOK

    async def _fetch_trades(self, limit):
        return []

    async def _ws_events(self):
        script = self.sessions.pop(0) if self.sessions else "hang"
        if script == "fail":
            raise OSError("conexión rechazada")
        self._ws = SimpleNamespace(latency=self.ws_latency or 0.0)
        try:
            if script == "hang":
                await asyncio.Event().wait()
            for ev in script:
                if ev == "drop":
                    raise VenueError("caída")
                if ev == "wait":
                    await asyncio.sleep(0.03)
                    continue
                yield ev
            await asyncio.Event().wait()
        finally:
            self._ws = None


async def run(venue, until, timeout=2.0):
    events = []
    stream = venue.market_stream()
    try:
        async with asyncio.timeout(timeout):
            async for ev in stream:
                events.append(ev)
                if until(events):
                    return events
    finally:
        await stream.aclose()


async def run_for(venue, seconds):
    """All the stream emits during `seconds`."""
    events = []
    stream = venue.market_stream()
    try:
        async with asyncio.timeout(seconds):
            async for ev in stream:
                events.append(ev)
    except TimeoutError:
        pass
    finally:
        await stream.aclose()
    return events


def states(events):
    return [(event.source, event.status) for event in events if isinstance(event, ConnState)]


def count(events, state):
    return states(events).count(state)


@pytest.mark.spec("RF-14")
class TestBackoff:
    def test_exponential_up_to_cap(self):
        backoff = Backoff(base_s=0.5, max_s=30, rng=lambda: 0.5)
        assert [backoff.delay(attempt) for attempt in range(1, 9)] == [0.5, 1, 2, 4, 8, 16, 30, 30]

    def test_jitter_20_percent(self):
        low = Backoff(base_s=0.5, max_s=30, rng=lambda: 0.0)
        high = Backoff(base_s=0.5, max_s=30, rng=lambda: 1.0)
        assert low.delay(3) == pytest.approx(1.6) and high.delay(3) == pytest.approx(2.4)

    def test_jitter_stays_under_the_cap(self):
        assert Backoff(base_s=0.5, max_s=30, rng=lambda: 1.0).delay(20) == 30

    def test_invalid_n(self):
        with pytest.raises(ValueError):
            Backoff(base_s=0.5, max_s=30).delay(0)


@pytest.mark.spec("RF-14")
class TestLatency:
    def test_moving_average(self):
        meter = LatencyMeter(alpha=0.5)
        assert meter.ms is None
        meter.add(10)
        meter.add(20)
        assert meter.ms == pytest.approx(15)

    def test_ignores_invalid_samples(self):
        meter = LatencyMeter()
        meter.add(0)  # websockets gives 0.0 before the first ping
        meter.add(-1)
        assert meter.ms is None


@pytest.mark.spec("RF-01", "RF-14", "CA-06")
class TestSupervisor:
    async def test_up_on_first_book_not_on_connecting(self):
        venue = FakeVenue(["wait", WS_BOOK])
        events = await run(venue, lambda evs: WS_BOOK in evs)
        assert events == [ConnState("nk", "market", "ws", "up", None, 1_000), WS_BOOK]

    async def test_drop_retries_ws_without_rest(self):
        venue = FakeVenue([WS_BOOK, "drop"], [WS_BOOK])
        events = await run(venue, lambda evs: count(evs, ("ws", "up")) == 2)
        assert states(events) == [("ws", "up"), ("ws", "reconnecting"), ("ws", "up")]
        assert venue.rest_calls == 0
        assert venue.delays == [0.01]

    async def test_backoff_grows_with_consecutive_failures(self):
        venue = FakeVenue("fail", "fail", [WS_BOOK])
        await run(venue, lambda evs: WS_BOOK in evs)
        assert venue.delays == [0.01, 0.02]

    async def test_three_failures_start_rest_until_ws_returns(self):
        venue = FakeVenue("fail", "fail", "fail", "fail", "fail", [WS_BOOK])
        events = await run(venue, lambda evs: WS_BOOK in evs)
        assert states(events) == [
            ("ws", "reconnecting"),
            ("ws", "reconnecting"),
            ("ws", "reconnecting"),
            ("rest", "up"),
            ("ws", "up"),
        ]
        assert REST_BOOK in events
        assert events[-2:] == [ConnState("nk", "market", "ws", "up", None, 1_000), WS_BOOK]
        assert venue.delays == [0.01, 0.02, 0.04, 0.08, 0.08]

    async def test_polling_stops_when_ws_returns(self):
        venue = FakeVenue("fail", "fail", "fail", [WS_BOOK, TRADE])
        events = await run(venue, lambda evs: TRADE in evs)
        calls = venue.rest_calls
        await asyncio.sleep(0.05)
        assert venue.rest_calls == calls
        i = events.index(WS_BOOK)
        assert REST_BOOK not in events[i:]

    async def test_data_resets_the_counter(self):
        """failure, failure, session with data that drops, failure: never 3 in a row."""
        venue = FakeVenue("fail", "fail", [WS_BOOK, "drop"], "fail", [WS_BOOK])
        events = await run(venue, lambda evs: count(evs, ("ws", "up")) == 2)
        assert venue.rest_calls == 0
        assert ("rest", "up") not in states(events)
        assert venue.delays == [0.01, 0.02, 0.01, 0.02]

    async def test_connecting_without_data_counts_as_failure(self):
        venue = FakeVenue(["drop"], ["drop"], ["drop"], [WS_BOOK])
        events = await run(venue, lambda evs: WS_BOOK in evs)
        assert ("rest", "up") in states(events)

    async def test_rest_down(self):
        venue = FakeVenue("fail", "fail", "fail", rest_ok=False)
        events = await run(venue, lambda evs: ("rest", "down") in states(evs))
        assert ("rest", "up") not in states(events)

    async def test_with_rest_active_reconnecting_is_not_reemitted(self):
        venue = FakeVenue(*["fail"] * 6, [WS_BOOK])
        events = await run(venue, lambda evs: WS_BOOK in evs)
        assert count(events, ("ws", "reconnecting")) == 3


@pytest.mark.spec("RF-14")
class TestStreamLatency:
    async def test_heartbeat_with_ws_latency(self):
        venue = FakeVenue([WS_BOOK], ws_latency=0.012, latency_every_s=0.02)
        events = await run(venue, lambda evs: count(evs, ("ws", "up")) >= 3)
        beats = [event for event in events if isinstance(event, ConnState)][1:]
        assert all(beat.latency_ms == pytest.approx(12) for beat in beats)

    async def test_heartbeat_with_rest_latency(self):
        venue = FakeVenue("fail", "fail", "fail", latency_every_s=0.02)
        events = await run(venue, lambda evs: count(evs, ("rest", "up")) >= 3)
        beats = [
            event for event in events if isinstance(event, ConnState) and event.source == "rest"
        ]
        assert beats[-1].latency_ms is not None and beats[-1].latency_ms >= 5

    async def test_no_measures_latency_is_none(self):
        venue = FakeVenue([WS_BOOK], latency_every_s=0.02)
        events = await run(venue, lambda evs: count(evs, ("ws", "up")) >= 2)
        assert all(event.latency_ms is None for event in events if isinstance(event, ConnState))

    async def test_no_heartbeat_while_reconnecting(self):
        """Reconnecting there is no latency to give: the state does not repeat."""
        venue = FakeVenue([WS_BOOK, "drop"], "hang", latency_every_s=0.02)
        events = await run_for(venue, 0.15)
        assert states(events) == [("ws", "up"), ("ws", "reconnecting")]


@pytest.mark.spec("RF-01")
async def test_programming_error_is_not_swallowed():
    """A bug (not a network failure) must reach the consumer, not be retried."""

    class Buggy(FakeVenue):
        async def _ws_events(self):
            raise KeyError("bug")
            yield  # pragma: no cover

    with pytest.raises(KeyError):
        await run(Buggy(), lambda evs: False)


@pytest.mark.spec("RF-01")
async def test_programming_error_in_polling_is_not_swallowed():
    class BuggyRest(FakeVenue):
        async def book_snapshot(self, depth):
            raise ZeroDivisionError("bug en el REST")

    with pytest.raises(ZeroDivisionError):
        await run(BuggyRest("fail", "fail", "fail"), lambda evs: False)
