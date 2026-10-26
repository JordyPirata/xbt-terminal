"""MarketHub: streams de mercado -> book, velas, estado de conexión.

Por cada venue:
1. Carga el mercado y el histórico de velas de cada timeframe por REST. Si
   falla, lo reintenta cada `retry_s` y publica el estado `(rest, down)`.
2. Consume `market_stream()`:
   - Book y deltas van a `L2Book`.
   - Los trades van a los `CandleBuilder`. Se descartan los anteriores a la
     carga, que ya cuentan en el histórico y sumarían volumen dos veces.
   - Los `ConnState` se guardan para la barra de estado.

Los cambios se avisan a los suscriptores como `(venue, qué)`, con `qué` ∈
market | book | candles | conn. La UI decide cuándo redibujar (throttle). Los
eventos que el hub acepta (book, trades que no descarta, conexión) también se
pasan tal cual a quien los pida, para reenviarlos por el socket (D-66). Los
huecos del book los repara cada adaptador (D-10, D-11), así que el hub no pide
snapshots.

Cada venue corre aparte bajo `supervised` (RNF-14, D-28): un bug en uno se
reporta a `faults`, marca su mercado caído y se reintenta con backoff, sin
parar al otro.

Spec: RF-01, RF-02, RF-03, RF-14, RNF-14 · design.md §6.1 · Tareas: T-12, T-13, T-27, T-62
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field

import httpx

from ..adapters.base import MarketData, MarketEvent, VenueError, describe
from ..adapters.streaming import now_ms
from ..core.book import L2Book
from ..core.candles import CandleBuilder
from ..core.models import (
    BookDelta,
    BookSnapshot,
    ConnState,
    MarketSpec,
    Timeframe,
    Trade,
    VenueId,
)
from .faults import Faults, supervised

log = logging.getLogger(__name__)

TIMEFRAMES: tuple[Timeframe, ...] = ("1m", "5m", "1h", "4h", "1d")
Listener = Callable[[VenueId, str], None]
EventListener = Callable[[VenueId, BookSnapshot | BookDelta | Trade | ConnState], None]


@dataclass
class VenueMarket:
    """Estado vivo de mercado de un venue."""

    id: VenueId
    book: L2Book
    candles: dict[Timeframe, CandleBuilder]
    spec: MarketSpec | None = None
    conn: ConnState | None = None
    ready: bool = False  # mercado e histórico cargados
    error: str | None = None
    loaded_at: int = 0  # trades anteriores ya están en el histórico
    conn_at: int | None = None  # cuándo llegó el último ConnState (reloj del hub)
    last_trade: Trade | None = field(default=None)


class MarketHub:
    def __init__(
        self,
        venues: Mapping[VenueId, MarketData],
        *,
        history: int = 300,
        retry_s: float = 5.0,
        clock: Callable[[], int] = now_ms,
        faults: Faults | None = None,
    ) -> None:
        self._venues = dict(venues)
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
        """Cada evento de mercado que el hub aplica, después de aplicarlo."""
        self._event_listeners.append(listener)

    def _emit(self, venue: VenueId, event: BookSnapshot | BookDelta | Trade | ConnState) -> None:
        for listener in self._event_listeners:
            listener(venue, event)

    def _notify(self, venue: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(venue, what)

    async def run(self) -> None:
        """Mantiene vivos todos los venues hasta que se cancela."""
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
        """Cierra los clientes HTTP de los venues que los tengan."""
        for venue in self._venues.values():
            close = getattr(venue, "aclose", None)
            if close is not None:
                await close()

    @property
    def clock(self) -> Callable[[], int]:
        """Reloj local (ms epoch) del hub; la app lo comparte."""
        return self._clock

    def clock_skew_ms(self, vid: VenueId) -> int | None:
        """Desfase del reloj del venue contra el local, si el adaptador lo mide
        (RNF-16): los venues falsos de los tests y el dry-run no tienen por qué."""
        return getattr(self._venues[vid], "clock_skew_ms", None)

    def roll(self, now: int | None = None) -> None:
        """Abre velas planas hasta `now` aunque no haya trades."""
        now = self._clock() if now is None else now
        for vid, st in self.state.items():
            if st.ready:
                for builder in st.candles.values():
                    builder.roll(now)
                self._notify(vid, "candles")

    async def _run_venue(self, vid: VenueId, venue: MarketData) -> None:
        st = self.state[vid]
        await self._load(vid, venue, st)
        async with aclosing(venue.market_stream()) as stream:
            async for event in stream:
                self.apply(vid, event)

    async def _load(self, vid: VenueId, venue: MarketData, st: VenueMarket) -> None:
        while True:
            try:
                spec = await venue.market()
                history = {tf: await venue.candles(tf, self._history) for tf in TIMEFRAMES}
                for tf, candles in history.items():
                    st.candles[tf].load(candles)  # ValueError si vienen desalineadas
            except (VenueError, httpx.HTTPError, ValueError) as exc:
                st.error = describe(exc)
                log.warning("%s: no se pudo cargar el mercado: %s", vid, st.error)
                self.apply(vid, ConnState(vid, "market", "rest", "down", None, self._clock()))
                await asyncio.sleep(self._retry_s)
                continue
            st.spec = spec
            st.loaded_at, st.ready, st.error = self._clock(), True, None
            log.info("%s: mercado cargado", vid)
            self._notify(vid, "market")
            self._notify(vid, "candles")
            return

    def apply(self, vid: VenueId, event: MarketEvent) -> None:
        st = self.state[vid]
        if isinstance(event, BookSnapshot | BookDelta):
            st.book.apply(event, received_at=self._clock())
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
