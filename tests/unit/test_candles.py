import pytest

from xbt_terminal.core.candles import TIMEFRAME_MS, CandleBuilder
from xbt_terminal.core.models import Candle, D, Trade

MIN = 60_000
T0 = 1_759_996_800_000  # exact multiple of 1 h


def trade(ts: int, price: str, quantity: str = "0.1") -> Trade:
    return Trade("nk", D(price), D(quantity), "buy", ts)


def candle(ts_open: int, o: str, h: str, low: str, c: str, v: str) -> Candle:
    return Candle(ts_open, D(o), D(h), D(low), D(c), D(v))


def test_t0_aligned_to_the_hour():
    assert T0 % TIMEFRAME_MS["1h"] == 0


@pytest.mark.spec("RF-03")
class TestLiveTrades:
    def test_trades_of_same_minute(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0 + 1_000, "1000", "0.1"))
        b.on_trade(trade(T0 + 20_000, "1010", "0.2"))
        b.on_trade(trade(T0 + 40_000, "995", "0.3"))
        b.on_trade(trade(T0 + 59_999, "1002", "0.4"))
        assert b.candles == (candle(T0, "1000", "1010", "995", "1002", "1.0"),)

    def test_candle_change(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.on_trade(trade(T0 + MIN, "1005"))
        assert [c.ts_open for c in b.candles] == [T0, T0 + MIN]
        assert b.candles[-1].open == D("1005")

    def test_minute_without_trades_filled_flat(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.on_trade(trade(T0 + 3 * MIN + 5, "1020"))
        c = b.candles
        assert [x.ts_open for x in c] == [T0 + i * MIN for i in range(4)]
        assert c[1] == candle(T0 + MIN, "1000", "1000", "1000", "1000", "0")
        assert c[3].open == D("1020")

    def test_late_trade_ignored(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0 + MIN, "1000"))
        b.on_trade(trade(T0 + 10, "500"))
        assert b.candles == (candle(T0 + MIN, "1000", "1000", "1000", "1000", "0.1"),)

    @pytest.mark.parametrize(
        "tf,step", [("5m", 5 * MIN), ("1h", 60 * MIN), ("4h", 240 * MIN), ("1d", 1440 * MIN)]
    )
    def test_other_timeframes(self, tf, step):
        start = T0 - T0 % step  # 4h and 1d start at exact multiples (00:00 UTC)
        b = CandleBuilder(tf)
        b.on_trade(trade(start + step - 1, "1000"))
        b.on_trade(trade(start + step, "1001"))
        assert [c.ts_open for c in b.candles] == [start, start + step]

    def test_day_starts_at_0_utc(self):
        b = CandleBuilder("1d")
        b.on_trade(trade(1_792_540_800_000 + 5 * 3_600_000, "1000"))  # 2026-10-20 05:00 UTC
        assert b.candles[0].ts_open == 1_792_540_800_000  # 2026-10-20 00:00 UTC


@pytest.mark.spec("RF-03")
class TestHistory:
    def test_trade_updates_last_history_candle(self):
        b = CandleBuilder("1m")
        b.load(
            [
                candle(T0 - MIN, "990", "995", "985", "992", "3"),
                candle(T0, "992", "999", "991", "998", "1"),
            ]
        )
        b.on_trade(trade(T0 + 30_000, "1003", "0.5"))
        assert b.candles[-1] == candle(T0, "992", "1003", "991", "1003", "1.5")
        assert len(b.candles) == 2

    def test_load_sorts_and_replaces(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1"))
        b.load([candle(T0, "2", "2", "2", "2", "0"), candle(T0 - MIN, "1", "1", "1", "1", "0")])
        assert [c.ts_open for c in b.candles] == [T0 - MIN, T0]

    def test_load_fills_history_gaps(self):
        b = CandleBuilder("1m")
        b.load([candle(T0, "1", "1", "1", "1", "1"), candle(T0 + 2 * MIN, "3", "3", "3", "3", "1")])
        assert [c.ts_open for c in b.candles] == [T0, T0 + MIN, T0 + 2 * MIN]

    def test_load_rejects_misaligned_candles(self):
        with pytest.raises(ValueError):
            CandleBuilder("5m").load([candle(T0 + MIN, "1", "1", "1", "1", "0")])

    def test_stored_candle_limit(self):
        b = CandleBuilder("1m", max_candles=3)
        for i in range(5):
            b.on_trade(trade(T0 + i * MIN, str(1000 + i)))
        assert [c.ts_open for c in b.candles] == [T0 + 2 * MIN, T0 + 3 * MIN, T0 + 4 * MIN]

    def test_huge_gap_makes_no_extra_candles(self):
        b = CandleBuilder("1m", max_candles=3)
        b.on_trade(trade(T0, "1000"))
        b.on_trade(trade(T0 + 10_000_000 * MIN, "1001"))
        c = b.candles
        assert [x.ts_open for x in c] == [T0 + (10_000_000 - k) * MIN for k in (2, 1, 0)]
        assert c[0].close == D("1000") and c[-1].open == D("1001")


@pytest.mark.spec("RF-03")
class TestRoll:
    def test_advances_even_without_trades(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.roll(T0 + 2 * MIN + 1)
        assert [c.ts_open for c in b.candles] == [T0, T0 + MIN, T0 + 2 * MIN]
        assert b.candles[-1].close == D("1000") and b.candles[-1].volume == D("0")

    def test_within_same_candle_does_nothing(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.roll(T0 + 59_999)
        assert len(b.candles) == 1

    def test_no_candles_does_nothing(self):
        b = CandleBuilder("1m")
        b.roll(T0)
        assert b.candles == ()
