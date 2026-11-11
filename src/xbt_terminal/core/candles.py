"""1m/5m/1h/4h/1d candles from history and live trades.

One `CandleBuilder` per venue and timeframe. It loads the REST history, then each
trade updates the current candle or opens the next. Periods without trades are
filled with flat candles at the last close (volume 0), so the chart has no gaps;
`roll(now)` does the same when time passes without trades. A trade older than the
current candle is ignored: the history wins.

Spec: RF-03 · design.md §6.1 · Task: T-11
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from .models import ZERO, Candle, Timeframe, Trade

TIMEFRAME_MS: dict[Timeframe, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,  # from 00:00 UTC, as on both venues (D-23)
}


class CandleBuilder:
    def __init__(self, timeframe: Timeframe, max_candles: int = 500) -> None:
        self.timeframe = timeframe
        self.step = TIMEFRAME_MS[timeframe]
        self._candles: deque[Candle] = deque(maxlen=max_candles)

    @property
    def candles(self) -> tuple[Candle, ...]:
        return tuple(self._candles)

    def load(self, history: Iterable[Candle]) -> None:
        """Replace everything with the REST history (in any order)."""
        ordered = sorted(history, key=lambda candle: candle.ts_open)
        for candle in ordered:
            if candle.ts_open % self.step:
                raise ValueError(f"vela {candle.ts_open} no alineada a {self.timeframe}")
        self._candles.clear()
        for candle in ordered:
            self._fill_until(candle.ts_open)
            self._candles.append(candle)

    def on_trade(self, trade: Trade) -> None:
        bucket = trade.ts - trade.ts % self.step
        if self._candles and bucket < self._candles[-1].ts_open:
            return
        self._fill_until(bucket)
        last = self._candles[-1] if self._candles else None
        if last is not None and last.ts_open == bucket:
            self._candles[-1] = Candle(
                ts_open=bucket,
                open=last.open,
                high=max(last.high, trade.price),
                low=min(last.low, trade.price),
                close=trade.price,
                volume=last.volume + trade.qty,
            )
        else:
            self._candles.append(
                Candle(bucket, trade.price, trade.price, trade.price, trade.price, trade.qty)
            )

    def roll(self, now: int) -> None:
        """Open flat candles up to the one containing `now`."""
        if self._candles:
            self._fill_until(now - now % self.step + self.step)

    def _fill_until(self, bucket: int) -> None:
        """Add flat candles between the last one and `bucket` (exclusive)."""
        if not self._candles:
            return
        close = self._candles[-1].close
        timestamp = self._candles[-1].ts_open + self.step
        if self._candles.maxlen is not None:  # after a long gap, only those that fit
            timestamp = max(timestamp, bucket - self._candles.maxlen * self.step)
        while timestamp < bucket:
            self._candles.append(Candle(timestamp, close, close, close, close, ZERO))
            timestamp += self.step
