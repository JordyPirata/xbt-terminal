"""Velas 1m/5m/1h/4h/1d a partir de histórico y trades en vivo.

Un `CandleBuilder` por venue y timeframe. Se carga con el histórico REST y luego
cada trade actualiza la vela en curso o abre la siguiente. Los periodos sin
trades se rellenan con velas planas al último cierre (volumen 0), para que el
gráfico no tenga huecos; `roll(now)` hace lo mismo cuando pasa el tiempo sin
trades. Un trade anterior a la vela en curso se ignora: el histórico manda.

Spec: RF-03 · design.md §6.1 · Tarea: T-11
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
    "1d": 86_400_000,  # desde las 00:00 UTC, como en los dos venues (D-23)
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
        """Reemplaza todo con el histórico REST (en cualquier orden)."""
        ordered = sorted(history, key=lambda c: c.ts_open)
        for c in ordered:
            if c.ts_open % self.step:
                raise ValueError(f"vela {c.ts_open} no alineada a {self.timeframe}")
        self._candles.clear()
        for c in ordered:
            self._fill_until(c.ts_open)
            self._candles.append(c)

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
        """Abre velas planas hasta la que contiene `now`."""
        if self._candles:
            self._fill_until(now - now % self.step + self.step)

    def _fill_until(self, bucket: int) -> None:
        """Añade velas planas entre la última y `bucket` (sin incluirla)."""
        if not self._candles:
            return
        close = self._candles[-1].close
        ts = self._candles[-1].ts_open + self.step
        if self._candles.maxlen is not None:  # tras un hueco largo, solo las que caben
            ts = max(ts, bucket - self._candles.maxlen * self.step)
        while ts < bucket:
            self._candles.append(Candle(ts, close, close, close, close, ZERO))
            ts += self.step
