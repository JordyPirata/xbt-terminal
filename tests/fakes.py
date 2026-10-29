"""Venue falso de datos de mercado para tests de servicios y UI: el test empuja
eventos con `push` y decide si `market()` y `candles()` fallan."""

from __future__ import annotations

import asyncio

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.core.candles import TIMEFRAME_MS
from xbt_terminal.core.models import BookSnapshot, Candle, D, Level, MarketSpec

T0 = 1_792_497_600_000  # alineado a la hora: T0 % 3_600_000 == 0


def spec(venue: str) -> MarketSpec:
    lot = D("0.0001") if venue == "nk" else D("0.00000001")
    return MarketSpec(
        venue=venue,  # type: ignore[arg-type]
        symbol="BTCB2/BTC" if venue == "nk" else "BTCB2_BTC",
        base="BTCB2",
        quote="BTC",
        tick_size=D("0.00000001"),
        lot_size=lot,
        min_qty=lot,
        min_notional=D("0"),
        maker_fee=D("0.002"),
        taker_fee=D("0.002"),
    )


def book(venue: str, bid: str, ask: str, seq: int | None = None, ts: int = T0) -> BookSnapshot:
    return BookSnapshot(
        venue,  # type: ignore[arg-type]
        bids=(Level(D(bid), D("1")), Level(D(bid) - D("0.00000100"), D("2"), amm=True)),
        asks=(Level(D(ask), D("0.5")), Level(D(ask) + D("0.00000100"), D("3"))),
        ts=ts,
        seq=seq,
    )


def history(step_ms: int, n: int = 5, start: int = T0) -> list[Candle]:
    return [
        Candle(start + i * step_ms, D("0.0072"), D("0.0073"), D("0.0071"), D("0.00725"), D("1"))
        for i in range(n)
    ]


class FakeFeed:
    """Cumple `MarketData`. Los eventos empujados salen por `market_stream`."""

    def __init__(self, venue: str, *, fail_market: int = 0) -> None:
        self.id = venue
        self.queue: asyncio.Queue = asyncio.Queue()
        self.fail_market = fail_market  # cuántas llamadas a market() fallan antes
        self.calls: list[str] = []
        # book REST para el contraste de D-76: un BookSnapshot, una excepción o
        # None (el de siempre)
        self.rest_book: BookSnapshot | Exception | None = None

    def push(self, *events) -> None:
        for ev in events:
            self.queue.put_nowait(ev)

    async def market(self) -> MarketSpec:
        self.calls.append("market")
        if self.fail_market:
            self.fail_market -= 1
            raise VenueError("sin red")
        return spec(self.id)

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        self.calls.append("book")
        if isinstance(self.rest_book, Exception):
            raise self.rest_book
        return self.rest_book or book(self.id, "0.00720000", "0.00730000")

    async def resync(self) -> None:
        self.calls.append("resync")

    async def recent_trades(self, limit: int):
        return []

    async def candles(self, tf, limit):
        self.calls.append(f"candles:{tf}:{limit}")
        step = TIMEFRAME_MS[tf]
        if step <= 3_600_000:
            return history(step)
        # 4h y 1d: alineadas a su paso (T0 no cae en día exacto) y acabando en T0
        return history(step, start=T0 - T0 % step - 4 * step)

    async def market_stream(self):
        while True:
            yield await self.queue.get()
