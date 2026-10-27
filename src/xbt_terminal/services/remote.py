"""Cliente del socket del daemon, para la pantalla `xbt` (D-66).

`RemoteEngine` mantiene la conexión (y la rehace si el daemon se va) y guarda
la última foto. `RemoteAccount` y `RemoteBot` dan de ella la misma cara que la
app usa de `AccountService` y `BotService`, así que los paneles no cambian.
`RemoteMarket` da la cara de `MarketHub` con el mercado que reenvía el daemon
y `RemoteOrders` la de `OrderService`: se prepara en el daemon, que devuelve
la propuesta y una referencia, y se confirma con la referencia (D-66). El
cliente no habla con los venues.
Lo único que se manda al daemon es `bot_on` y `bot_off` (D-61); una respuesta
con error, los avisos y los fallos del daemon salen como `Notice`.

Spec: RF-31 · design.md §6.13 · Tareas: T-53, T-62, T-63, T-64 · D-61, D-66
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.streaming import now_ms
from ..core.book import L2Book
from ..core.bot import BotState
from ..core.candles import CandleBuilder
from ..core.commands import CancelAll, CancelOrder, CancelVenue, DoubleOrder, PlaceOrder
from ..core.double import DoubleState
from ..core.models import ZERO, BookSnapshot, ConnState, OrderRequest, Trade, VenueId
from ..core.rebalance import FeeInfo
from ..core.results import Range, Report, Summary
from ..core.risk import Violation
from .account import VenueAccount
from .deposits import ASSETS, DepositRow
from .engine import Notice
from .faults import Faults
from .market import TIMEFRAMES, VenueMarket
from .orders import (
    CancelPlan,
    CancelRefused,
    CancelResult,
    DoubleRejected,
    DoubleSent,
    Failed,
    Pending,
    PendingDouble,
    Rejected,
    Sent,
    Uncertain,
)
from .wire import VENUES, ClientView, Fees, MarketView, Rows, Snapshot, decode, dumps, loads

log = logging.getLogger(__name__)

RETRY_S = 2.0
REPLIES: dict[str, type] = {
    c.__name__: c
    for c in (
        Pending, Rejected, Sent, Failed, Uncertain, PendingDouble, DoubleRejected, DoubleSent,
        CancelPlan, CancelRefused, CancelResult,
    )
}  # fmt: skip
LIMIT = 2**22  # una foto con muchas dobles cabe de sobra


class DaemonGone(Exception):
    pass


class DaemonRefused(Exception):
    """El daemon contestó a una petición con un error (el texto es para el usuario)."""


@dataclass(frozen=True, slots=True)
class Hello:
    mode: str
    has_bot: bool
    view: ClientView | None = None  # la config que necesita la pantalla (D-66)


async def _open(path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.open_unix_connection(str(path), limit=LIMIT)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise DaemonGone(
            f"no hay daemon escuchando en {path}: arráncalo con `xbt daemon` "
            "(o indica su socket con --socket)"
        ) from exc
    except OSError as exc:  # p. ej. una ruta de más de 107 bytes, o sin permiso
        raise DaemonGone(f"no se puede abrir el socket {path}: {exc}") from exc


async def probe(path: Path, timeout: float = 5.0) -> Hello:
    """Antes de abrir la pantalla: el modo del daemon y si tiene bot."""
    reader, writer = await _open(path)
    try:
        async with asyncio.timeout(timeout):
            hello = loads(await reader.readline())
            state = loads(await reader.readline())
    except (TimeoutError, ValueError) as exc:
        raise DaemonGone(f"el daemon de {path} no contesta bien: {exc}") from exc
    finally:
        writer.close()
    try:
        view = decode(ClientView | None, hello.get("view"))
    except (TypeError, ValueError) as exc:
        raise DaemonGone(f"el daemon de {path} manda una config que no se entiende: {exc}") from exc
    return Hello(str(hello["mode"]), state.get("state", {}).get("bot") is not None, view)


def _fresh(vid: VenueId) -> VenueMarket:
    return VenueMarket(vid, L2Book(vid), {tf: CandleBuilder(tf) for tf in TIMEFRAMES})


class RemoteMarket:
    """El mercado del daemon con la cara de `MarketHub` que usa la app: `state`,
    `subscribe`, `roll` y `clock`. Los trades que llegan ya pasaron el filtro
    del hub del daemon, así que se aplican todos."""

    def __init__(self, clock: Callable[[], int] = now_ms) -> None:
        self._clock = clock
        self.faults = Faults()  # vacío: los fallos de mercado son del daemon
        self.state: dict[VenueId, VenueMarket] = {vid: _fresh(vid) for vid in VENUES}
        self._listeners: list[Callable[[VenueId, str], None]] = []

    def subscribe(self, listener: Callable[[VenueId, str], None]) -> None:
        self._listeners.append(listener)

    def subscribe_events(self, listener: Callable[..., None]) -> None:
        pass  # nadie reenvía desde el cliente

    def _notify(self, venue: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(venue, what)

    @property
    def clock(self) -> Callable[[], int]:
        return self._clock

    def clock_skew_ms(self, vid: VenueId) -> int | None:
        return None  # el desfase lo vigila el daemon (RNF-16)

    def roll(self, now: int | None = None) -> None:
        now = self._clock() if now is None else now
        for vid, st in self.state.items():
            if st.ready:
                for builder in st.candles.values():
                    builder.roll(now)
                self._notify(vid, "candles")

    async def run(self) -> None:
        pass  # lo alimenta `RemoteEngine`

    async def aclose(self) -> None:
        pass

    # -- del socket ------------------------------------------------------------

    def _init(self, vid: VenueId, view: MarketView) -> None:
        st = _fresh(vid)
        st.spec, st.ready, st.error = view.spec, view.ready, view.error
        st.last_trade = view.last_trade
        for tf, candles in view.candles.items():
            st.candles[tf].load(candles)
        if view.book is not None:
            st.book.apply(view.book, received_at=self._clock())
        if view.conn is not None:
            st.conn, st.conn_at = view.conn, self._clock()
        self.state[vid] = st
        for what in ("market", "candles", "book", "conn"):
            self._notify(vid, what)

    def _book(self, vid: VenueId, book: BookSnapshot | None) -> None:
        st = self.state[vid]
        if book is None:
            st.book = L2Book(vid)
        else:
            st.book.apply(book, received_at=self._clock())
        self._notify(vid, "book")

    def _trade(self, vid: VenueId, trade: Trade) -> None:
        st = self.state[vid]
        st.last_trade = trade
        for builder in st.candles.values():
            builder.on_trade(trade)
        self._notify(vid, "candles")

    def _conn(self, vid: VenueId, conn: ConnState) -> None:
        st = self.state[vid]
        st.conn, st.conn_at = conn, self._clock()
        self._notify(vid, "conn")


class RemoteAccount:
    """Cuentas del daemon con la cara de `AccountService`."""

    def __init__(self) -> None:
        self.state: dict[VenueId, VenueAccount] = {}
        self.faults = Faults()  # vacío: los fallos son del daemon y llegan como avisos
        self._listeners: list[Callable[[VenueId, str], None]] = []

    def subscribe(self, listener: Callable[[VenueId, str], None]) -> None:
        self._listeners.append(listener)

    def subscribe_events(self, listener: Callable[..., None]) -> None:
        pass  # los fills los sigue el daemon

    def _update(self, accounts: dict[VenueId, VenueAccount]) -> None:
        self.state = accounts
        for vid in accounts:
            for what in ("conn", "balances", "orders"):
                self._notify(vid, what)

    def _notify(self, vid: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(vid, what)


EMPTY_REPORT = Report((), Summary(0, ZERO, None, None, None, 0, 0, 0, 0), ())


class RemoteResults:
    """Los resultados del daemon (RF-25): los calcula él con su journal y el
    precio del token. `refresh` los pide; `load` da el último de cada rango."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link
        self._reports: dict[Range, Report] = {}
        self.error: str | None = None

    def load(self, rng: Range) -> Report:
        return self._reports.get(rng, EMPTY_REPORT)

    async def refresh(self, rng: Range) -> str | None:
        """Pide el informe del rango; devuelve el error, si lo hubo."""
        try:
            _, raw, _ = await self._link.request("results", {"range": rng})
        except (DaemonGone, DaemonRefused) as exc:
            self.error = str(exc)
            return self.error
        self._reports[rng] = decode(Report, raw)
        self.error = None
        return None


class RemoteDeposits:
    """Las direcciones de depósito que pide el daemon (RF-28)."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link

    @property
    def venues(self) -> tuple[VenueId, ...]:
        snap = self._link.snapshot
        return tuple(snap.deposit_venues) if snap is not None else ()

    async def load(self) -> list[DepositRow]:
        try:
            _, raw, _ = await self._link.request("deposits", {})
        except (DaemonGone, DaemonRefused) as exc:  # cada fila lo dice, como un fallo del venue
            return [DepositRow(vid, a, None, str(exc)) for vid in self.venues for a in ASSETS]
        return list(decode(Rows, raw).rows)


class RemoteRebalance:
    """Las comisiones de retiro que pide el daemon (RF-29)."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link

    @property
    def venues(self) -> tuple[VenueId, ...]:
        snap = self._link.snapshot
        return tuple(snap.fee_venues) if snap is not None else ()

    async def load(self) -> dict[tuple[VenueId, str], FeeInfo]:
        try:
            _, raw, _ = await self._link.request("withdrawal_fees", {})
        except (DaemonGone, DaemonRefused) as exc:
            return {(vid, a): str(exc) for vid in self.venues for a in ASSETS}
        return {(r.venue, r.asset): r.fee for r in decode(Fees, raw).fees}


class RemoteBot:
    """El bot del daemon con la cara de `BotService`."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link
        self.state = BotState()
        self._text = "bot sin conexión con el daemon"
        self._summary = ""
        self._refusal: str | None = "sin conexión con el daemon"
        self._listeners: list[Callable[[BotState], None]] = []

    def subscribe(self, listener: Callable[[BotState], None]) -> None:
        self._listeners.append(listener)

    @property
    def summary(self) -> str:
        return self._summary

    def refusal(self) -> str | None:
        return self._refusal

    def status_text(self) -> str:
        return self._text

    def arm(self) -> str | None:
        """Se manda `bot_on`; si el daemon lo rechaza, llega como aviso."""
        self._link.send("bot_on")
        return None

    def disarm(self) -> None:
        self._link.send("bot_off")

    def _update(self, snap: Snapshot) -> None:
        if snap.bot is None:
            return
        view = snap.bot
        self._text, self._summary, self._refusal = view.status_text, view.summary, view.refusal
        changed = view.state != self.state
        self.state = view.state
        if changed:
            for listener in self._listeners:
                listener(view.state)


class RemoteOrders:
    """Las órdenes del daemon con la cara de `OrderService` que usa la app. La
    propuesta se guarda con su referencia hasta confirmarla; un rechazo del daemon
    al confirmar (propuesta caducada…) vuelve como el resultado de error que la app
    ya sabe enseñar. Sin daemon, `DaemonGone`."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link
        self._tokens: dict[int, tuple[object, str]] = {}  # id(propuesta) → (propuesta, token)
        self._listeners: list[Callable[[str], None]] = []

    # -- lo que lee la app ---------------------------------------------------------

    @property
    def doubles(self) -> dict[str, DoubleState]:
        return self._link.doubles

    @property
    def hanging(self) -> dict[str, str]:
        return self._link.hanging

    @property
    def uncertain(self) -> list[OrderRequest]:
        return self._link.uncertain

    @property
    def in_flight(self) -> int:
        return self._link.in_flight

    def subscribe_doubles(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def _changed(self, before: dict[str, DoubleState], after: dict[str, DoubleState]) -> None:
        for did, state in after.items():
            if before.get(did) != state:
                for listener in self._listeners:
                    listener(did)

    # -- peticiones ------------------------------------------------------------------

    async def _ask(self, cmd: str, args: dict[str, Any]) -> Any:
        kind, raw, token = await self._link.request(cmd, args)
        value = decode(REPLIES[kind], raw)
        if token is not None:
            self._tokens[id(value)] = (value, token)
        return value

    def _token(self, proposal: object) -> str:
        entry = self._tokens.pop(id(proposal), None)
        if entry is None or entry[0] is not proposal:
            raise DaemonRefused("esa propuesta no salió de este cliente")
        return entry[1]

    async def prepare(self, cmd: PlaceOrder) -> Pending | Rejected:
        return await self._ask("prepare", {"order": cmd})  # type: ignore[no-any-return]

    async def confirm(self, pending: Pending) -> Sent | Failed | Uncertain | Rejected:
        try:
            return await self._ask("confirm", {"token": self._token(pending)})  # type: ignore[no-any-return]
        except DaemonRefused as exc:
            return Failed(pending.req, f"el daemon no la envió: {exc}", local=True)

    async def prepare_double(self, cmd: DoubleOrder) -> PendingDouble | DoubleRejected:
        return await self._ask("prepare_double", {"order": cmd})  # type: ignore[no-any-return]

    async def confirm_double(self, pending: PendingDouble) -> DoubleSent | DoubleRejected:
        try:
            return await self._ask("confirm_double", {"token": self._token(pending)})  # type: ignore[no-any-return]
        except DaemonRefused as exc:
            why = (Violation("daemon", f"el daemon no la envió: {exc}"),)
            return DoubleRejected(pending.id, (Rejected(pending.buy.req, why),))

    async def plan_cancel(
        self, cmd: CancelOrder | CancelVenue | CancelAll
    ) -> CancelPlan | CancelRefused:
        args = {"kind": type(cmd).__name__, "value": cmd}
        return await self._ask("plan_cancel", args)  # type: ignore[no-any-return]

    async def execute_cancel(self, plan: CancelPlan) -> CancelResult:
        try:
            return await self._ask("execute_cancel", {"token": self._token(plan)})  # type: ignore[no-any-return]
        except DaemonRefused as exc:
            error = f"el daemon no canceló: {exc}"
            return CancelResult(
                {vid: len(o) for vid, o in plan.orders.items()},
                {vid: error for vid in plan.orders},
            )


class RemoteEngine:
    def __init__(self, path: Path, *, has_bot: bool, retry_s: float = RETRY_S) -> None:
        self.path = path
        self._retry_s = retry_s
        self.mode: str | None = None
        self.snapshot: Snapshot | None = None
        self.connected = False
        self.account = RemoteAccount()
        self.market = RemoteMarket()
        self.bot: RemoteBot | None = RemoteBot(self) if has_bot else None
        self.orders = RemoteOrders(self)
        self.results = RemoteResults(self)
        self.deposits = RemoteDeposits(self)
        self.rebalance = RemoteRebalance(self)
        self._writer: asyncio.StreamWriter | None = None
        self._ids = itertools.count(1)
        self._waiting: dict[int, asyncio.Future[tuple[str, Any, str | None]]] = {}
        self._listeners: list[Callable[[Notice], None]] = []
        self._link_listeners: list[Callable[[bool], None]] = []

    # -- lo que lee la app -------------------------------------------------------

    @property
    def doubles(self) -> dict[str, DoubleState]:
        return self.snapshot.doubles if self.snapshot is not None else {}

    @property
    def hanging(self) -> dict[str, str]:
        return self.snapshot.hanging if self.snapshot is not None else {}

    @property
    def uncertain(self) -> list[OrderRequest]:
        return self.snapshot.uncertain if self.snapshot is not None else []

    @property
    def in_flight(self) -> int:
        return self.snapshot.in_flight if self.snapshot is not None else 0

    @property
    def faults(self) -> int:
        """Fallos de la sesión del daemon."""
        return self.snapshot.faults if self.snapshot is not None else 0

    @property
    def can_trade(self) -> dict[VenueId, bool]:
        return self.snapshot.can_trade if self.snapshot is not None else {}

    def subscribe(self, listener: Callable[[Notice], None]) -> None:
        self._listeners.append(listener)

    def subscribe_link(self, listener: Callable[[bool], None]) -> None:
        """Se conectó (True) o se perdió el daemon (False)."""
        self._link_listeners.append(listener)

    def _say(self, text: str, *, error: bool = False) -> None:
        notice = Notice(text, error)
        for listener in self._listeners:
            listener(notice)

    # -- conexión ------------------------------------------------------------------

    def send(self, cmd: str) -> None:
        if self._writer is None or not self.connected:
            self._say(f"sin conexión con el daemon: {cmd} no se mandó", error=True)
            return
        self._writer.write(dumps({"cmd": cmd}))

    async def request(self, cmd: str, args: dict[str, Any]) -> tuple[str, Any, str | None]:
        """Manda una petición y espera su respuesta: `(kind, valor sin decodificar,
        token)`. Error del daemon → `DaemonRefused`; sin daemon → `DaemonGone`."""
        if self._writer is None or not self.connected:
            raise DaemonGone("sin conexión con el daemon: no se mandó nada")
        rid = next(self._ids)
        future: asyncio.Future[tuple[str, Any, str | None]] = (
            asyncio.get_running_loop().create_future()
        )
        self._waiting[rid] = future
        try:
            self._writer.write(dumps({"id": rid, "cmd": cmd, "args": args}))
            return await future
        finally:
            self._waiting.pop(rid, None)

    def _answered(self, msg: dict[str, Any]) -> bool:
        """Respuesta a una petición de `request`: True si era una."""
        future = self._waiting.get(msg.get("id"))  # type: ignore[arg-type]
        if future is None:
            return False
        if not future.done():
            if msg.get("error") is not None:
                future.set_exception(DaemonRefused(str(msg["error"])))
            else:
                future.set_result((str(msg["kind"]), msg.get("value"), msg.get("token")))
        return True

    def _abandon(self) -> None:
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(DaemonGone("se perdió la conexión con el daemon"))
        self._waiting.clear()

    async def run(self) -> None:
        """Conecta, lee y, si el daemon se va, reintenta hasta que se cancela."""
        while True:
            try:
                reader, self._writer = await _open(self.path)
            except DaemonGone:
                await asyncio.sleep(self._retry_s)
                continue
            try:
                await self._read(reader)
            except (ConnectionError, OSError, ValueError) as exc:
                log.warning("conexión con el daemon cortada: %s", exc)
            finally:
                self._writer.close()
                self._writer = None
                self._abandon()
                if self.connected:
                    self._set_link(False)
                    self._say("⚠ conexión con el daemon perdida: reintentando", error=True)
            await asyncio.sleep(self._retry_s)

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while line := await reader.readline():
            msg = loads(line)
            try:
                self._handle(msg)
            except (KeyError, TypeError, ValueError) as exc:  # uno raro no corta el resto
                log.warning("mensaje del daemon que no se entiende (%s): %s", msg.get("type"), exc)

    def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind in ("reply", "error") and self._answered(msg):
            return
        if kind == "hello":
            mode = str(msg["mode"])
            if self.mode is not None and mode != self.mode:
                self._say(f"⚠ el daemon ahora corre en {mode} (antes {self.mode})", error=True)
            self.mode = mode
            if not self.connected:
                self._set_link(True)
        elif kind == "state":
            before = self.doubles
            self.snapshot = decode(Snapshot, msg["state"])
            self.orders._changed(before, self.snapshot.doubles)
            self.account._update(self.snapshot.accounts)
            if self.bot is not None:
                self.bot._update(self.snapshot)
        elif kind in ("market_init", "book", "trade", "conn"):
            self._market(kind, decode(VenueId, msg["venue"]), msg)
        elif kind == "fill":
            self.account._notify(decode(VenueId, msg["venue"]), "fill")
        elif kind == "notice":
            self._say(str(msg["text"]), error=bool(msg["error"]))
        elif kind == "fault":
            self._say(f"daemon: {msg['message']}", error=True)
        elif kind == "reply" and msg.get("error"):
            self._say(str(msg["error"]), error=True)
        elif kind == "error":
            self._say(f"el daemon no aceptó la orden: {msg['error']}", error=True)

    def _market(self, kind: str, vid: VenueId, msg: dict[str, Any]) -> None:
        if kind == "market_init":
            self.market._init(vid, decode(MarketView, msg["market"]))
        elif kind == "book":
            self.market._book(vid, decode(BookSnapshot | None, msg["book"]))
        elif kind == "trade":
            self.market._trade(vid, decode(Trade, msg["trade"]))
        else:
            self.market._conn(vid, decode(ConnState, msg["conn"]))

    def _set_link(self, up: bool) -> None:
        self.connected = up
        for listener in self._link_listeners:
            listener(up)

    async def aclose(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await self._writer.wait_closed()
            self._writer = None
