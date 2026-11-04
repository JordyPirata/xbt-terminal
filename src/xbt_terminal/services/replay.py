"""Reproducción de una grabación (RF-34, RF-35, D-80): venues simulados bajo el
`Engine` de siempre.

- `ReplayVenue` cumple `MarketData`: saca los eventos con sus tiempos relativos.
- `SimVenue` opera contra el book de un `ReplayVenue`: la orden llega tras
  `latency_ms`, casa como taker (`core.replay.match`) y lo demás queda abierto.
- `VirtualLoop` es un bucle de asyncio sin esperas: el reloj salta al siguiente
  temporizador. Con él, el informe tarda segundos y sale siempre igual.

Bot, RiskCheck, journal y resultados no saben que esto es una reproducción.

Spec: RF-34, RF-35, CA-19, CA-20 · design.md §6.14 · Tarea: T-87
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

MATCH_DEPTH = 200  # niveles por lado contra los que casa una orden
PROBE_DEPTH = 50  # los mismos que mira el bot
PLENTY = Decimal(10**9)  # saldo de sobra para contar cruces
TAIL_S = 30.0  # tras la grabación, para que cierre la última doble
CLOCK_RESOLUTION_S = 1e-6
_ARMED = BotState(status="armed")


class Timeline:
    """Origen de tiempos común a los venues de una reproducción."""

    def __init__(self, info: RecordingInfo, clock: Callable[[], int], *, venues: int) -> None:
        self._info = info
        self._clock = clock
        self._pending = venues
        self._offset: int | None = None
        self.done = asyncio.Event()

    @property
    def offset(self) -> int:
        """Ahora menos el primer evento; se fija la primera vez que se pide."""
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
    """El evento en el tiempo de la reproducción: el hub tira los trades "de
    antes de cargar", y la pantalla pinta velas con estas horas."""
    if isinstance(event, ConnState):
        return replace(event, since=event.since + offset)
    if isinstance(event, BookSnapshot | BookDelta | Trade):
        return replace(event, ts=event.ts + offset)
    return event


class ReplayVenue:
    """`MarketData` que lee una grabación; sin red."""

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
        self.on_book: Callable[[], None] = lambda: None  # lo usa `SimVenue`

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
        """El book reproducido: el contraste de RNF-24 coincide siempre."""
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
        await self._until(self.timeline.last + offset)  # un venue no acaba antes que el otro
        self.timeline.finish()
        yield ConnState(self.id, "market", "ws", "down", None, self._clock())
        await asyncio.Event().wait()  # sin más mercado, hasta que paren el daemon


class SimVenue(DryRunVenue):
    """Órdenes contra el book de la grabación, con un competidor que se lleva
    niveles enteros. El book grabado no cambia con lo casado, pero lo casado no
    se vuelve a casar: queda gastado hasta que la grabación mueva ese nivel (D-80)."""

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
        # (lado, precio) → (cantidad grabada del nivel, gastado de ella)
        self._spent: dict[tuple[Side, Decimal], tuple[Decimal, Decimal]] = {}

    def _taken(self, level: object) -> bool:
        return Decimal(self._rng.randrange(10_000)) / 100 < self._competitor_pct

    def _on_book(self) -> None:
        """La grabación movió el book: lo gastado de un nivel que cambió o ya no
        está deja de contar."""
        book = self._feed.book.snapshot(MATCH_DEPTH)
        if not self._spent or book is None:
            return
        now = {("buy", lv.price): lv.qty for lv in book.asks}
        now |= {("sell", lv.price): lv.qty for lv in book.bids}
        self._spent = {key: spent for key, spent in self._spent.items() if now.get(key) == spent[0]}

    def _unspent(self, side: Side, levels: tuple[Level, ...]) -> list[Level]:
        """Los niveles con lo que les queda."""
        out = []
        for level in levels:
            left = level.qty - self._spent.get((side, level.price), (ZERO, ZERO))[1]
            if left > 0:
                out.append(replace(level, qty=left))
        return out

    def _spend(self, side: Side, levels: tuple[Level, ...], price: Decimal, qty: Decimal) -> None:
        recorded = next(lv.qty for lv in levels if lv.price == price)
        used = self._spent.get((side, price), (recorded, ZERO))[1]
        self._spent[side, price] = (recorded, used + qty)

    async def place_limit(self, req: OrderRequest) -> Order:
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        order = await super().place_limit(req)
        book = self._feed.book.snapshot(MATCH_DEPTH)
        if book is None:
            return order
        levels = book.asks if req.side == "buy" else book.bids
        left = self._unspent(req.side, levels)
        for price, qty in match(req.side, req.price, req.qty, left, self._taken):
            self._spend(req.side, levels, price, qty)
            order = await self.simulate_fill(order.order_id, qty, price=price)
        return order


class _VirtualSelector(selectors.DefaultSelector):
    """En vez de esperar, avanza el reloj del bucle hasta el siguiente temporizador."""

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
        # con la hora epoch, sumar 1 ns no mueve un float: un temporizador que
        # vence justo ahora no llegaría a dispararse nunca
        self._clock_resolution = CLOCK_RESOLUTION_S

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def now_ms(self) -> int:
        return round(self._now * 1000)


class CrossingProbe:
    """Cuenta los cruces sobre el umbral: cada tramo seguido en que la decisión
    del bot dispararía con saldo de sobra, sin cooldown ni topes (RF-35)."""

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
        for vid, st in self._hub.state.items():
            book = st.book.snapshot(PROBE_DEPTH)
            if st.spec is None or book is None:
                return
            views[vid] = VenueView(st.spec, book.bids, book.asks, PLENTY, PLENTY)
        crossed = isinstance(decide(views, _ARMED, self._limits, self._clock()), Fire)
        self.count += crossed and not self._open
        self._open = crossed


async def run_report(engine: Engine, timeline: Timeline, *, tail_s: float = TAIL_S) -> None:
    """Los servicios del motor hasta acabar la grabación, con el bot armado. Una
    pierna colgada lo rearma: aquí se mide, no se opera (RF-35)."""
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
