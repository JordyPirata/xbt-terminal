"""Base común de los adaptadores con WS público y respaldo REST, y supervisor
de conexión.

Cada venue implementa `book_snapshot`, `_fetch_trades` (REST) y `_ws_events`
(una sesión WS abierta con `ws_connect`: entrega datos y lanza al caer). Esta
base pone el resto:

- `market_stream` y su supervisor (D-11):
  - Reconexión con backoff exponencial y jitter.
  - Al tercer fallo seguido, polling REST en paralelo hasta que vuelva el WS.
  - Emite `ConnState` y reemite el estado cada `latency_every_s` con la
    latencia (media móvil del RTT de ping WS o de las peticiones REST).
- Deduplicación de trades por id entre REST, lote inicial del WS y trades en
  vivo, para que una reconexión no repita trades ya entregados.
- `parsing`: un campo que falta o no encaja es un cambio de API → VenueError.
- `sending`: un envío del que no se sabe si el venue lo aplicó → UncertainError
  (RNF-12, D-26).
- `timed`: cada respuesta REST con cabecera `Date` actualiza `clock_skew_ms`,
  el desfase del reloj del venue contra el local (RNF-16, D-30).

Spec: RF-01, RF-14, CA-06, RNF-03, RNF-09, RNF-12, RNF-16 · design.md §4, §6.1
Tareas: T-08, T-09, T-12, T-25, T-29
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx
import websockets

from ..core.models import BookSnapshot, ConnState, ConnStatus, Source, Trade, VenueId
from .base import MarketEvent, UncertainError, VenueError, describe

log = logging.getLogger(__name__)

T = TypeVar("T")

TRADES_EVERY_POLLS = 5  # respaldo REST: trades cada 5 vueltas de book (D-09)
FALLBACK_TRADES = 100  # trades por vuelta del respaldo: de sobra para 5 s
SEEN_TRADES = 1000
WS_ERRORS = (OSError, TimeoutError, websockets.WebSocketException, VenueError)
# Al cerrar no se espera más que esto la respuesta del venue: con la cola de
# recepción llena (16 frames sin leer) el cierre limpio tardaría los 10 s por defecto.
WS_CLOSE_TIMEOUT_S = 1.0
FALLBACK_AFTER_FAILURES = 3  # design.md §6.1
# La petición no llegó a salir: un envío que falla así no creó nada (D-26)
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
PARSE_ERRORS = (KeyError, TypeError, ValueError, ArithmeticError)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def skew_ms(date: str | None, sent_ms: int, received_ms: int) -> int | None:
    """Hora del venue menos la local, en ms, a partir de la cabecera `Date` de una
    respuesta pedida en `sent_ms` y recibida en `received_ms` (hora local). `Date`
    va truncada al segundo: se toma su mitad, así que el error es ±0,5 s más la
    mitad del RTT. None si no hay cabecera o no se entiende."""
    if not date:
        return None
    try:
        server = parsedate_to_datetime(date)
    except (TypeError, ValueError):
        return None
    server_ms = int(server.timestamp() * 1000) + 500
    return server_ms - (sent_ms + received_ms) // 2


def iso_ms(text: str) -> int:
    """'2026-10-20T14:23:36.539Z' → ms epoch, sin pasar por float."""
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"fecha sin zona horaria: {text!r}")
    return (dt - _EPOCH) // _MS


class SeenIds:
    """Ids de trade ya entregados, acotado a los últimos `size`."""

    def __init__(self, size: int = SEEN_TRADES) -> None:
        self._order: deque[str] = deque()
        self._ids: set[str] = set()
        self._size = size

    def add(self, trade_id: str) -> bool:
        """True si es nuevo."""
        if trade_id in self._ids:
            return False
        self._order.append(trade_id)
        self._ids.add(trade_id)
        if len(self._order) > self._size:
            self._ids.discard(self._order.popleft())
        return True


class Backoff:
    """Espera antes del reintento n (n ≥ 1): `min(max, base × 2^(n-1))` con ±20 %
    de jitter, sin pasar nunca de `max`."""

    def __init__(
        self, base_s: float, max_s: float, rng: Callable[[], float] = random.random
    ) -> None:
        self.base_s, self.max_s, self._rng = base_s, max_s, rng

    def delay(self, n: int) -> float:
        if n < 1:
            raise ValueError(f"reintento inválido: {n}")
        raw = min(self.max_s, self.base_s * 2 ** (n - 1))
        return min(self.max_s, raw * (0.8 + 0.4 * self._rng()))


class LatencyMeter:
    """Media móvil exponencial en ms. Las muestras ≤ 0 se ignoran (websockets
    da 0.0 hasta el primer ping)."""

    def __init__(self, alpha: float = 0.3) -> None:
        self._alpha = alpha
        self.ms: float | None = None

    def add(self, ms: float) -> None:
        if ms <= 0:
            return
        self.ms = ms if self.ms is None else self.ms + self._alpha * (ms - self.ms)


@dataclass(frozen=True, slots=True)
class _Crash:
    """Error que no es de red (un bug): se relanza en el consumidor."""

    exc: BaseException


class StreamingVenue:
    id: VenueId
    name: str
    max_trades: int  # límite de trades por petición REST del venue

    def __init__(
        self,
        *,
        ws_url: str,
        book_depth: int,
        fallback_every_s: float = 1.0,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 30.0,
        fallback_after: int = FALLBACK_AFTER_FAILURES,
        ping_interval_s: float = 5.0,
        latency_every_s: float = 5.0,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._ws_url = ws_url
        self._book_depth = book_depth
        self._fallback_every_s = fallback_every_s
        self._backoff = Backoff(backoff_base_s, backoff_max_s, rng)
        self._fallback_after = fallback_after
        self._ping_interval_s = ping_interval_s
        self._latency_every_s = latency_every_s
        self._clock = clock
        self._seen = SeenIds()
        self._ws: Any = None  # conexión abierta, para leer su latencia
        self._ws_latency = LatencyMeter()
        self._rest_latency = LatencyMeter()
        self._current: ConnState | None = None
        self.clock_skew_ms: int | None = None  # venue - local, de la última `Date` (RNF-16)

    # -- a implementar por cada venue -----------------------------------------

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        raise NotImplementedError

    async def _fetch_trades(self, limit: int) -> list[tuple[str, Trade]]:
        """(id, Trade) de los trades más recientes por REST, en cualquier orden."""
        raise NotImplementedError

    def _ws_events(self) -> AsyncIterator[MarketEvent]:
        """Una sesión WS: datos (book, deltas, trades); lanza o termina al caer."""
        raise NotImplementedError

    # -- utilidades para los venues --------------------------------------------

    @asynccontextmanager
    async def ws_connect(self) -> AsyncIterator[Any]:
        async with websockets.connect(
            self._ws_url,
            open_timeout=10,
            close_timeout=WS_CLOSE_TIMEOUT_S,
            ping_interval=self._ping_interval_s,
        ) as ws:
            self._ws = ws
            await ws.ping()  # primera medida de latencia sin esperar al keepalive
            try:
                yield ws
            finally:
                self._ws = None

    async def timed(self, request: Awaitable[T]) -> T:
        """Espera una petición REST y anota su duración como latencia y, si trae
        cabecera `Date`, el desfase de reloj con el venue."""
        start, sent = time.perf_counter(), self._clock()
        result = await request
        self._rest_latency.add((time.perf_counter() - start) * 1000)
        if isinstance(result, httpx.Response):
            skew = skew_ms(result.headers.get("date"), sent, self._clock())
            if skew is not None:
                self.clock_skew_ms = skew
        return result

    def parsing(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except VenueError:
            raise
        except PARSE_ERRORS as exc:
            raise VenueError(f"{self.name}: respuesta inesperada ({exc!r})") from exc

    async def sending(self, request: Awaitable[Any], parse: Callable[[Any], T]) -> T:
        """Espera un envío que crea algo en el venue y lee la respuesta con `parse`.
        Si no se sabe si el venue lo aplicó, UncertainError (D-26): fallo de red
        después de conectar, HTTP 5xx, 2xx que no es JSON o respuesta ilegible.
        Sin conectar, 4xx o error explícito del venue, el error sale tal cual."""
        try:
            payload = await request
        except NOT_SENT:
            raise
        except httpx.HTTPError as exc:
            detail = f"{type(exc).__name__} {exc}".strip()
            raise UncertainError(f"{self.name}: sin respuesta clara ({detail})") from exc
        except UncertainError:
            raise
        except VenueError as exc:
            status = exc.status
            if status is not None and (status >= 500 or (exc.unreadable and status < 400)):
                raise UncertainError(str(exc), status=status) from exc
            raise
        try:
            return parse(payload)
        except (*PARSE_ERRORS, VenueError) as exc:
            raise UncertainError(f"{self.name}: respuesta al envío ilegible ({exc})") from exc

    async def recent_trades(self, limit: int) -> list[Trade]:
        """Trades más recientes en orden cronológico. Los marca como vistos."""
        trades = await self._fetch_trades(min(limit, self.max_trades))
        for trade_id, _ in trades:
            self._seen.add(trade_id)
        return sorted((t for _, t in trades), key=lambda t: t.ts)

    def fresh(self, trades: list[tuple[str, Trade]]) -> list[Trade]:
        """Solo los no entregados aún, en orden cronológico."""
        return sorted((t for i, t in trades if self._seen.add(i)), key=lambda t: t.ts)

    # -- estado y latencia -----------------------------------------------------

    def _latency_ms(self, source: Source) -> float | None:
        if source == "ws":
            if self._ws is not None:
                self._ws_latency.add(self._ws.latency * 1000)
            return self._ws_latency.ms
        return self._rest_latency.ms

    def state(self, source: Source, status: ConnStatus) -> ConnState:
        latency = self._latency_ms(source) if status == "up" else None
        return ConnState(self.id, "market", source, status, latency, since=self._clock())

    # -- supervisor --------------------------------------------------------------

    async def market_stream(self) -> AsyncIterator[MarketEvent]:
        """Eventos de mercado de la fuente que esté viva, con `ConnState` al
        cambiar de estado y como latido. Un bug (no un fallo de red) se relanza."""
        queue: asyncio.Queue[MarketEvent | _Crash] = asyncio.Queue()
        self._current = None

        def emit(event: MarketEvent) -> None:
            if isinstance(event, ConnState):
                self._current = event
            queue.put_nowait(event)

        async def guarded(work: Awaitable[None]) -> None:
            try:
                await work
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                queue.put_nowait(_Crash(exc))

        def spawn(work: Awaitable[None]) -> asyncio.Task[None]:
            return asyncio.create_task(guarded(work))

        tasks = [spawn(self._supervise(emit, spawn)), spawn(self._heartbeat(emit))]
        try:
            while True:
                item = await queue.get()
                if isinstance(item, _Crash):
                    raise item.exc
                yield item
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _supervise(
        self,
        emit: Callable[[MarketEvent], None],
        spawn: Callable[[Awaitable[None]], asyncio.Task[None]],
    ) -> None:
        failures = 0
        poller: asyncio.Task[None] | None = None
        try:
            while True:
                got_book = False
                cause = "sesión cerrada"
                try:
                    async with aclosing(self._ws_events()) as events:
                        async for event in events:
                            if not got_book and isinstance(event, BookSnapshot):
                                got_book, failures = True, 0
                                log.info("%s: WS conectado", self.name)
                                if poller is not None:
                                    await _stop(poller)
                                    poller = None
                                    log.info("%s: fin del polling REST de respaldo", self.name)
                                emit(self.state("ws", "up"))
                            emit(event)
                except WS_ERRORS as exc:
                    cause = f"{type(exc).__name__}: {exc}"
                failures += 1
                delay = self._backoff.delay(failures)
                log.warning(
                    "%s: WS caído (%s); reintento en %.1f s", self.name, cause, delay
                )  # fmt: skip
                if poller is None:
                    emit(self.state("ws", "reconnecting"))
                    if failures >= self._fallback_after:
                        log.warning(
                            "%s: polling REST de respaldo tras %d fallos del WS",
                            self.name, failures,
                        )  # fmt: skip
                        poller = spawn(self._poll(emit))
                await asyncio.sleep(delay)
        finally:
            if poller is not None:
                await _stop(poller)

    async def _poll(self, emit: Callable[[MarketEvent], None]) -> None:
        """Polling REST de respaldo hasta que lo cancele el supervisor."""
        status: ConnStatus | None = None
        n = 0
        while True:
            try:
                events: list[MarketEvent] = [await self.book_snapshot(self._book_depth)]
                if n % TRADES_EVERY_POLLS == 0:
                    limit = min(FALLBACK_TRADES, self.max_trades)
                    events += self.fresh(await self._fetch_trades(limit))
                new_status: ConnStatus = "up"
            except (httpx.HTTPError, VenueError) as exc:
                events, new_status = [], "down"
                if status != "down":
                    log.warning("%s: REST de respaldo caído: %s", self.name, describe(exc))
            if new_status != status:
                if new_status == "up":
                    log.info("%s: REST de respaldo con datos", self.name)
                status = new_status
                emit(self.state("rest", status))
            for event in events:
                emit(event)
            n += 1
            await asyncio.sleep(self._fallback_every_s)

    async def _heartbeat(self, emit: Callable[[MarketEvent], None]) -> None:
        """Reemite el estado vivo con la latencia actual; reconectando, nada."""
        while True:
            await asyncio.sleep(self._latency_every_s)
            current = self._current
            if current is not None and current.status == "up":
                emit(replace(current, latency_ms=self._latency_ms(current.source)))


async def _stop(task: asyncio.Task[Any]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
