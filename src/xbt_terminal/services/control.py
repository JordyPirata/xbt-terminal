"""Socket de control del daemon: lo que ve y hace el cliente (`xbt`, D-66).

Un socket Unix `0600` en el directorio de datos (que es `0700`): solo el mismo
usuario llega, y se entra por SSH (D-61). JSON por líneas (`wire`). Al
conectar, `hello` y la foto (`state`); después, `state` cada vez que algo
cambia (agrupado) y al menos cada `every_s`, y `notice` y `fault` según pasan.
El mercado también viaja (D-66): `market_init` por venue al conectar y cuando
el venue se vuelve a cargar; después `trade` y `conn` según pasan y `book`
agrupado cada `book_every_s`, con `book_depth` niveles por lado.
Del cliente (D-66): `ping`, `bot_on`, `bot_off` y las órdenes. `prepare`,
`prepare_double` y `plan_cancel` validan en el daemon y devuelven la propuesta
con un `token`; `confirm`, `confirm_double` y `execute_cancel` solo llevan el
token, que vale una vez y caduca a los `proposal_ttl_s`. Las peticiones con
`id` se contestan con `reply {id, kind, value}` o `reply {id, error}`, cada
una en su tarea. Cada cliente tiene su cola; si se llena (no lee), se le
cierra: un cliente lento nunca frena al motor. Todo lo que sale pasa por el
`Redactor` del journal.

El daemon tiene el lock (RNF-17), así que un socket que ya exista es de uno
que murió y se reemplaza.

Spec: RF-31, RNF-14, CA-07 · design.md §6.13 · Tareas: T-52, T-62, T-63 · D-61, D-66
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from .. import __version__
from ..core.commands import CancelAll, CancelOrder, CancelVenue, DoubleOrder, PlaceOrder
from ..core.models import BookDelta, BookSnapshot, ConnState, Trade, VenueId
from ..core.results import Range
from .engine import Engine, Notice
from .faults import Fault
from .orders import CancelPlan, OrderService, Pending, PendingDouble
from .wire import ClientView, FeeRow, Fees, Rows, decode, dumps, loads, market_view, snapshot

log = logging.getLogger(__name__)

SOCKET_NAME = "xbt.sock"
MarketEvent = BookSnapshot | BookDelta | Trade | ConnState
MAX_SOCKET_PATH = 107  # bytes: sun_path de Linux (108) menos el NUL final


def socket_problem(path: Path) -> str | None:
    """Qué impide abrir el socket en `path`, o None: una ruta larga no cabe."""
    size = len(os.fsencode(path))
    if size <= MAX_SOCKET_PATH:
        return None
    return (
        f"la ruta del socket {path} es demasiado larga ({size} bytes, máx. "
        f"{MAX_SOCKET_PATH}): indica otra con --socket"
    )


COALESCE_S = 0.02  # varios cambios seguidos, una sola foto
BOOK_EVERY_S = 0.1  # el book, como mucho diez veces por segundo y venue
BOOK_DEPTH = 50
PROPOSAL_TTL_S = 60.0  # lo que puede estar abierto el diálogo de confirmación
CANCELS: dict[str, type[CancelOrder | CancelVenue | CancelAll]] = {
    c.__name__: c for c in (CancelOrder, CancelVenue, CancelAll)
}
T = TypeVar("T")


class Refused(Exception):
    """Una petición que el daemon no atiende; el texto va al cliente."""


class _Client:
    def __init__(self, writer: asyncio.StreamWriter, queue_max: int) -> None:
        self.writer = writer
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(queue_max)
        self.task = asyncio.create_task(self._pump())

    def offer(self, line: bytes) -> bool:
        try:
            self.queue.put_nowait(line)
        except asyncio.QueueFull:
            return False
        return True

    async def _pump(self) -> None:
        try:
            while True:
                self.writer.write(await self.queue.get())
                await self.writer.drain()
        except (ConnectionError, OSError):
            pass  # se fue: el lector lo quita

    def close(self) -> None:
        self.task.cancel()
        self.writer.close()


class ControlServer:
    def __init__(
        self,
        engine: Engine,
        path: Path,
        *,
        every_s: float = 1.0,
        queue_max: int = 256,
        book_every_s: float = BOOK_EVERY_S,
        book_depth: int = BOOK_DEPTH,
        proposal_ttl_s: float = PROPOSAL_TTL_S,
        view: ClientView | None = None,
    ) -> None:
        self._engine = engine
        self.path = path
        self._every_s = every_s
        self._queue_max = queue_max
        self._book_every_s = book_every_s
        self._book_depth = book_depth
        self._books: set[VenueId] = set()  # venues con el book cambiado sin mandar
        self._book_ticker: asyncio.Task[None] | None = None
        self._proposal_ttl_s = proposal_ttl_s
        self._view = view
        self._proposals: dict[str, tuple[Any, float]] = {}  # token → (propuesta, caduca)
        self._asking: set[asyncio.Task[None]] = set()  # peticiones en curso
        self._clients: set[_Client] = set()
        self._server: asyncio.Server | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._dirty = asyncio.Event()
        self._subscribed = False

    @property
    def clients(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        self.path.unlink(missing_ok=True)
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))
        finally:
            os.umask(old)
        self.path.chmod(0o600)
        self._subscribe()
        self._ticker = asyncio.create_task(self._tick())
        self._book_ticker = asyncio.create_task(self._tick_books())
        log.info("socket de control en %s", self.path)

    async def aclose(self) -> None:
        if self._server is None:
            return
        server, self._server = self._server, None
        for ticker in (self._ticker, self._book_ticker):
            if ticker is not None:
                ticker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ticker
        server.close()
        for client in list(self._clients):
            client.close()
        self._clients.clear()
        await server.wait_closed()
        self.path.unlink(missing_ok=True)

    # -- del motor a los clientes -------------------------------------------------

    def _subscribe(self) -> None:
        if self._subscribed:  # los servicios no tienen baja: una vez por servidor
            return
        self._subscribed = True
        engine = self._engine
        engine.subscribe(self._on_notice)
        for faults in engine.faults:
            faults.subscribe(self._on_fault)
        if engine.bot is not None:
            engine.bot.subscribe(lambda _state: self._dirty.set())
        if engine.account is not None:
            engine.account.subscribe(self._on_account)
        if engine.orders is not None:
            engine.orders.subscribe_doubles(lambda _id: self._dirty.set())
        engine.hub.subscribe(self._on_hub)
        engine.hub.subscribe_events(self._on_market)

    def _on_hub(self, venue: VenueId, what: str) -> None:
        if what == "market":  # cargado (o recargado): foto entera de ese venue
            self._guarded(lambda: self._broadcast(self._market_init(venue)))

    def _on_market(self, venue: VenueId, event: MarketEvent) -> None:
        self._guarded(lambda: self._forward(venue, event))

    def _guarded(self, work: Callable[[], None]) -> None:
        """Lo llama el hub: un bug al reenviar no puede tumbar su venue (RNF-14)."""
        try:
            work()
        except Exception as exc:
            if self._engine.faults:
                self._engine.faults[0].report("control", exc)
            else:
                log.exception("socket de control: fallo al reenviar el mercado")

    def _forward(self, venue: VenueId, event: MarketEvent) -> None:
        if isinstance(event, Trade):
            self._broadcast({"type": "trade", "venue": venue, "trade": event})
        elif isinstance(event, ConnState):
            self._broadcast({"type": "conn", "venue": venue, "conn": event})
        else:
            self._books.add(venue)

    def _market_init(self, venue: VenueId) -> dict[str, Any]:
        view = market_view(self._engine.hub.state[venue], self._book_depth)
        return {"type": "market_init", "venue": venue, "market": view}

    async def _tick_books(self) -> None:
        while True:
            await asyncio.sleep(self._book_every_s)
            venues, self._books = self._books, set()
            for venue in sorted(venues):
                book = self._engine.hub.state[venue].book.snapshot(self._book_depth)
                self._broadcast({"type": "book", "venue": venue, "book": book})

    def _on_account(self, venue: VenueId, what: str) -> None:
        if what == "fill":  # el cliente rehace sus resultados (RF-25)
            self._broadcast({"type": "fill", "venue": venue})
        if what in ("balances", "orders", "fill", "conn"):
            self._dirty.set()

    def _on_notice(self, notice: Notice) -> None:
        self._broadcast({"type": "notice", "text": notice.text, "error": notice.error})
        self._dirty.set()

    def _on_fault(self, fault: Fault) -> None:
        self._broadcast({"type": "fault", "message": fault.message})
        self._dirty.set()

    async def _tick(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._dirty.wait(), self._every_s)
                await asyncio.sleep(COALESCE_S)
            self._dirty.clear()
            if self._clients:
                state = self._state()
                if state is not None:
                    self._broadcast(state)

    def _state(self) -> dict[str, Any] | None:
        try:
            return {"type": "state", "state": snapshot(self._engine)}
        except Exception as exc:  # un bug aquí no puede tumbar el daemon (RNF-14)
            if self._engine.faults:
                self._engine.faults[0].report("control", exc)
            return None

    def _line(self, message: dict[str, Any]) -> bytes:
        line = dumps(message)
        journal = self._engine.journal
        if journal is None:
            return line
        return journal.redactor.redact(line.decode()).encode()

    def _broadcast(self, message: dict[str, Any]) -> None:
        if self._server is None or not self._clients:
            return
        line = self._line(message)
        for client in list(self._clients):
            self._send(client, line)

    def _send(self, client: _Client, line: bytes) -> None:
        if not client.offer(line):
            log.warning("socket de control: un cliente no lee; se le desconecta")
            self._drop(client)

    def _drop(self, client: _Client) -> None:
        if client in self._clients:
            self._clients.discard(client)
            client.close()

    # -- de los clientes al motor -------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # la foto inicial (hello, state, un market_init por venue) no cuenta en la cola
        client = _Client(writer, self._queue_max + 2 + len(self._engine.hub.state))
        self._clients.add(client)
        hello = {
            "type": "hello", "mode": self._engine.mode, "version": __version__,
            "view": self._view,
        }  # fmt: skip
        self._send(client, self._line(hello))
        state = self._state()
        if state is not None:
            self._send(client, self._line(state))
        for venue in self._engine.hub.state:  # sin await entre medias: nada se cuela
            self._send(client, self._line(self._market_init(venue)))
        try:
            while client in self._clients:
                line = await reader.readline()
                if not line:
                    break
                # cada petición en su tarea: un envío lento no frena las demás
                task = asyncio.create_task(self._reply(client, line))
                self._asking.add(task)  # referencia fuerte: si no, el GC se la lleva
                task.add_done_callback(self._asking.discard)
        except (ConnectionError, OSError, ValueError):
            pass  # cortado, o una línea sin fin de más de 64 KiB
        finally:
            self._drop(client)
            # las que estén en vuelo terminan (un envío no se corta a medias, D-29)

    async def _reply(self, client: _Client, line: bytes) -> None:
        answer = await self._answer(line)
        if client in self._clients:
            self._send(client, self._line(answer))

    async def _answer(self, line: bytes) -> dict[str, Any]:
        try:
            msg = loads(line)
        except ValueError as exc:
            return {"type": "error", "error": str(exc)}
        cmd, rid = msg.get("cmd"), msg.get("id")
        head: dict[str, Any] = {"type": "reply", "cmd": cmd}
        if rid is not None:
            head["id"] = rid
        if cmd == "ping":
            return {"type": "pong"} if rid is None else {**head, "kind": "pong", "value": None}
        if cmd in ("bot_on", "bot_off"):
            return {**head, "error": self._bot(cmd)}
        work = self._commands.get(cmd) if isinstance(cmd, str) else None
        if work is None:
            error = {"type": "error", "error": f"orden no admitida: {cmd!r}"}
            return error if rid is None else {**error, "id": rid}
        args = msg.get("args") or {}
        try:
            value, token = await work(args if isinstance(args, dict) else {})
        except Refused as exc:
            return {**head, "error": str(exc)}
        except (KeyError, TypeError, ValueError) as exc:
            return {**head, "error": f"petición mal formada: {exc}"}
        except Exception as exc:  # un bug no tumba el daemon (RNF-14)
            if self._engine.faults:
                self._engine.faults[0].report("control", exc)
            return {**head, "error": f"fallo del daemon: {exc}"}
        finally:
            self._dirty.set()
        reply = {**head, "kind": type(value).__name__, "value": value}
        if token is not None:
            reply["token"] = token
        return reply

    def _bot(self, cmd: str) -> str | None:
        bot = self._engine.bot
        if bot is None:
            return "no hay [bot] en la config"
        if cmd == "bot_on":
            error = bot.arm()
            log.info("bot on desde un cliente%s", f": {error}" if error else "")
        else:
            bot.disarm()
            error = None
            log.info("bot off desde un cliente")
        self._dirty.set()
        return error

    # -- órdenes: propuesta con referencia, confirmación con la referencia --------

    @property
    def _commands(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[tuple[Any, str | None]]]]:
        return {
            "prepare": self._prepare,
            "confirm": self._confirm,
            "prepare_double": self._prepare_double,
            "confirm_double": self._confirm_double,
            "plan_cancel": self._plan_cancel,
            "execute_cancel": self._execute_cancel,
            "results": self._results,
            "deposits": self._deposits,
            "withdrawal_fees": self._withdrawal_fees,
        }

    def _propose(self, value: Any) -> str:
        now = asyncio.get_running_loop().time()
        self._proposals = {k: v for k, v in self._proposals.items() if v[1] > now}
        token = secrets.token_hex(8)
        self._proposals[token] = (value, now + self._proposal_ttl_s)
        return token

    def _take(self, args: dict[str, Any], kind: type[T]) -> T:
        """La propuesta del token, una sola vez: se quita antes de ningún await."""
        entry = self._proposals.pop(str(args.get("token")), None)
        if entry is None or entry[1] <= asyncio.get_running_loop().time():
            raise Refused("propuesta caducada o ya usada: vuelve a pedir la orden")
        value = entry[0]
        if not isinstance(value, kind):
            raise Refused("esa referencia es de otra clase de propuesta")
        return value

    @property
    def _orders(self) -> OrderService:
        orders = self._engine.orders
        if orders is None:
            raise Refused("el daemon está en solo lectura: no hay órdenes")
        return orders

    async def _prepare(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        result = await self._orders.prepare(decode(PlaceOrder, args["order"]))
        return result, self._propose(result) if isinstance(result, Pending) else None

    async def _confirm(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        return await self._orders.confirm(self._take(args, Pending)), None

    async def _prepare_double(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        result = await self._orders.prepare_double(decode(DoubleOrder, args["order"]))
        return result, self._propose(result) if isinstance(result, PendingDouble) else None

    async def _confirm_double(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        return await self._orders.confirm_double(self._take(args, PendingDouble)), None

    async def _plan_cancel(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        kind = CANCELS.get(str(args.get("kind")))
        if kind is None:
            raise Refused(f"cancelación desconocida: {args.get('kind')!r}")
        result = await self._orders.plan_cancel(decode(kind, args.get("value") or {}))
        return result, self._propose(result) if isinstance(result, CancelPlan) else None

    async def _execute_cancel(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        return await self._orders.execute_cancel(self._take(args, CancelPlan)), None

    # -- lo demás que el cliente no puede hacer solo (D-66) -----------------------

    async def _results(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        results = self._engine.results
        if results is None:
            raise Refused("el daemon no tiene resultados (sin journal)")
        rng = decode(Range, args["range"])
        await results.refresh_prices()
        # leer los journals de todo el rango es disco: fuera del loop
        return await asyncio.to_thread(results.load, rng), None

    async def _deposits(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        deposits = self._engine.deposits
        if deposits is None or not deposits.venues:
            raise Refused("depósitos: el daemon no tiene keys de ningún venue")
        return Rows(tuple(await deposits.load())), None

    async def _withdrawal_fees(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        rebalance = self._engine.rebalance
        if rebalance is None or not rebalance.venues:
            raise Refused("reequilibrio: el daemon no tiene keys de ningún venue")
        fees = await rebalance.load()
        return Fees(tuple(FeeRow(vid, asset, fee) for (vid, asset), fee in fees.items())), None
