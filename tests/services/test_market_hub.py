"""MarketHub: REST history + each venue's stream → book, candles and state."""

import asyncio

import pytest

from xbt_terminal.core.models import BookDelta, ConnState, D, Level, Trade
from xbt_terminal.services.market import MarketHub

from ..fakes import T0, FakeFeed, book

NOW = T0 + 5 * 3_600_000  # after the fake venue's 1h history


class Hub:
    """Start a MarketHub with fake venues and let it wait for conditions."""

    def __init__(self, **feeds):
        self.feeds = feeds or {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.changes: list[tuple[str, str]] = []
        self.now = NOW
        self.hub = MarketHub(self.feeds, history=50, retry_s=0.01, clock=lambda: self.now)
        self.hub.subscribe(lambda venue, what: self.changes.append((venue, what)))

    async def __aenter__(self):
        self.task = asyncio.create_task(self.hub.run())
        return self

    async def __aexit__(self, *error):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def until(self, predicate, timeout=1.0):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.002)


@pytest.mark.spec("RF-01", "RF-03")
class TestStartup:
    async def test_loads_market_and_history_of_each_timeframe(self):
        async with Hub() as hub:
            await hub.until(
                lambda: all(market_state.ready for market_state in hub.hub.state.values())
            )
            for venue, feed in hub.feeds.items():
                state = hub.hub.state[venue]
                assert state.spec.venue == venue
                assert feed.calls[:6] == [
                    "market",
                    "candles:1m:50",
                    "candles:5m:50",
                    "candles:1h:50",
                    "candles:4h:50",
                    "candles:1d:50",
                ]
                assert {tf: len(builder.candles) for tf, builder in state.candles.items()} == {
                    "1m": 5,
                    "5m": 5,
                    "1h": 5,
                    "4h": 5,
                    "1d": 5,
                }
            assert ("nk", "market") in hub.changes and ("nx", "candles") in hub.changes

    async def test_startup_failure_retries_and_says_so(self):
        feeds = {"nk": FakeFeed("nk", fail_market=2), "nx": FakeFeed("nx")}
        async with Hub(**feeds) as hub:
            await hub.until(lambda: hub.hub.state["nk"].ready)
            assert feeds["nk"].calls.count("market") == 3
            assert ("nk", "conn") in hub.changes
            assert hub.hub.state["nk"].error is None  # cleared on recovery

    async def test_down_state_until_started(self):
        feeds = {"nk": FakeFeed("nk", fail_market=10**6), "nx": FakeFeed("nx")}
        async with Hub(**feeds) as hub:
            await hub.until(lambda: hub.hub.state["nk"].conn is not None)
            state = hub.hub.state["nk"]
            assert (state.conn.source, state.conn.status) == ("rest", "down")
            assert "sin red" in state.error and not state.ready


@pytest.mark.spec("RF-01", "RF-02")
class TestBook:
    async def test_snapshot_and_delta(self):
        async with Hub() as hub:
            await hub.until(lambda: hub.hub.state["nk"].ready)
            hub.feeds["nk"].push(book("nk", "0.00720000", "0.00730000", seq=1))
            await hub.until(lambda: hub.hub.state["nk"].book.valid)
            hub.feeds["nk"].push(BookDelta("nk", (Level(D("0.00725000"), D("4")),), (), T0, 2))
            await hub.until(lambda: hub.hub.state["nk"].book.best_bid == D("0.00725000"))
            assert ("nk", "book") in hub.changes
            assert hub.hub.state["nx"].book.best_bid is None  # each venue its own


@pytest.mark.spec("RF-03")
class TestCandleBars:
    async def test_live_trade_updates_all_timeframes(self):
        async with Hub() as hub:
            await hub.until(lambda: hub.hub.state["nx"].ready)
            hub.feeds["nx"].push(Trade("nx", D("0.0080"), D("2"), "buy", NOW + 1_000))
            await hub.until(lambda: hub.hub.state["nx"].last_trade is not None)
            for builder in hub.hub.state["nx"].candles.values():
                assert builder.candles[-1].close == D("0.0080")

    async def test_trades_before_load_do_not_count_twice(self):
        """The initial WS batch brings trades already in the REST history."""
        async with Hub() as hub:
            await hub.until(lambda: hub.hub.state["nx"].ready)
            before = hub.hub.state["nx"].candles["1h"].candles
            hub.feeds["nx"].push(
                Trade("nx", D("0.0099"), D("50"), "buy", NOW - 1_000),
                ConnState("nx", "market", "ws", "up", None, NOW),
            )
            await hub.until(lambda: hub.hub.state["nx"].conn is not None)
            assert hub.hub.state["nx"].candles["1h"].candles == before

    async def test_roll_opens_flat_candles(self):
        async with Hub() as hub:
            await hub.until(lambda: hub.hub.state["nk"].ready)
            candle_count = len(hub.hub.state["nk"].candles["1m"].candles)
            hub.hub.roll(T0 + 10 * 60_000)
            assert len(hub.hub.state["nk"].candles["1m"].candles) > candle_count


@pytest.mark.spec("RF-14")
async def test_keeps_last_connection_state():
    async with Hub() as hub:
        await hub.until(lambda: hub.hub.state["nk"].ready)
        state = ConnState("nk", "market", "ws", "up", 120.0, NOW)
        hub.feeds["nk"].push(state)
        await hub.until(lambda: hub.hub.state["nk"].conn == state)
        assert ("nk", "conn") in hub.changes


@pytest.mark.spec("RF-03")
async def test_misaligned_candles_are_venue_error_not_hub():
    class Misaligned(FakeFeed):
        async def candles(self, tf, limit):
            return [
                candle.__class__(
                    candle.ts_open + 1,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                )
                for candle in await super().candles(tf, limit)
            ]

    feeds = {"nk": Misaligned("nk"), "nx": FakeFeed("nx")}
    async with Hub(**feeds) as hub:
        await hub.until(lambda: hub.hub.state["nx"].ready and hub.hub.state["nk"].error)
        assert "no alineada" in hub.hub.state["nk"].error
        assert not hub.hub.state["nk"].ready
