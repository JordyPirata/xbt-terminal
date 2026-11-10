"""Client of the daemon socket, for the `xbt` screen (D-66).

`RemoteEngine` keeps the connection (and rebuilds it if the daemon leaves) and holds
the last snapshot. `RemoteAccount` and `RemoteBot` give from it the same face the app
uses of `AccountService` and `BotService`, so panels do not change. `RemoteMarket`
gives `MarketHub`'s face with the market the daemon forwards and `RemoteOrders`
`OrderService`'s: preparing happens in the daemon, which returns the proposal and a
reference, and confirming uses the reference (D-66). The client does not talk to the
venues.
All that is sent to the daemon is `bot_on` and `bot_off` (D-61); an error reply, the
daemon's notices and its faults come out as `Notice`.

Spec: RF-31 · design.md §6.13 · Tasks: T-53, T-62, T-63, T-64 · D-61, D-66
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
LIMIT = 2**22  # a snapshot with many doubles fits easily


class DaemonGoneError(Exception):
    pass


class DaemonRefusedError(Exception):
    """The daemon answered a request with an error (the text is for the user)."""


@dataclass(frozen=True, slots=True)
class Hello:
    mode: str
    has_bot: bool
    view: ClientView | None = None  # the config the screen needs (D-66)


async def _open(path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.open_unix_connection(str(path), limit=LIMIT)
    except (FileNotFoundError, ConnectionRefusedError) as error:
        raise DaemonGoneError(
            f"no hay daemon escuchando en {path}: arráncalo con `xbt daemon` "
            "(o indica su socket con --socket)"
        ) from error
    except OSError as error:  # e.g. a path over 107 bytes, or no permission
        raise DaemonGoneError(f"no se puede abrir el socket {path}: {error}") from error


async def probe(path: Path, timeout: float = 5.0) -> Hello:
    """Before opening the screen: the daemon's mode and whether it has a bot."""
    reader, writer = await _open(path)
    try:
        async with asyncio.timeout(timeout):
            hello = loads(await reader.readline())
            state = loads(await reader.readline())
    except (TimeoutError, ValueError) as error:
        raise DaemonGoneError(f"el daemon de {path} no contesta bien: {error}") from error
    finally:
        writer.close()
    try:
        view = decode(ClientView | None, hello.get("view"))
    except (TypeError, ValueError) as error:
        message = f"el daemon de {path} manda una config que no se entiende: {error}"
        raise DaemonGoneError(message) from error
    return Hello(str(hello["mode"]), state.get("state", {}).get("bot") is not None, view)


def _fresh(venue_id: VenueId) -> VenueMarket:
    return VenueMarket(venue_id, L2Book(venue_id), {tf: CandleBuilder(tf) for tf in TIMEFRAMES})


class RemoteMarket:
    """The daemon's market with the `MarketHub` face the app uses: `state`, `subscribe`,
    `roll` and `clock`. Trades arriving already passed the daemon hub's filter, so all
    are applied."""

    def __init__(self, clock: Callable[[], int] = now_ms) -> None:
        self._clock = clock
        self.faults = Faults()  # empty: market faults are the daemon's
        self.state: dict[VenueId, VenueMarket] = {venue_id: _fresh(venue_id) for venue_id in VENUES}
        self._listeners: list[Callable[[VenueId, str], None]] = []

    def subscribe(self, listener: Callable[[VenueId, str], None]) -> None:
        self._listeners.append(listener)

    def subscribe_events(self, listener: Callable[..., None]) -> None:
        pass  # nobody forwards from the client

    def _notify(self, venue: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(venue, what)

    @property
    def clock(self) -> Callable[[], int]:
        return self._clock

    def clock_skew_ms(self, venue_id: VenueId) -> int | None:
        return None  # the daemon watches the skew (RNF-16)

    def roll(self, now: int | None = None) -> None:
        now = self._clock() if now is None else now
        for venue_id, state in self.state.items():
            if state.ready:
                for builder in state.candles.values():
                    builder.roll(now)
                self._notify(venue_id, "candles")

    async def run(self) -> None:
        pass  # fed by `RemoteEngine`

    async def aclose(self) -> None:
        pass

    # -- from the socket ------------------------------------------------------------

    def _init(self, venue_id: VenueId, view: MarketView) -> None:
        state = _fresh(venue_id)
        state.spec, state.ready, state.error = view.spec, view.ready, view.error
        state.last_trade = view.last_trade
        for tf, candles in view.candles.items():
            state.candles[tf].load(candles)
        if view.book is not None:
            state.book.apply(view.book, received_at=self._clock())
        if view.conn is not None:
            state.conn, state.conn_at = view.conn, self._clock()
        self.state[venue_id] = state
        for what in ("market", "candles", "book", "conn"):
            self._notify(venue_id, what)

    def _book(self, venue_id: VenueId, book: BookSnapshot | None) -> None:
        state = self.state[venue_id]
        if book is None:
            state.book = L2Book(venue_id)
        else:
            state.book.apply(book, received_at=self._clock())
        self._notify(venue_id, "book")

    def _trade(self, venue_id: VenueId, trade: Trade) -> None:
        state = self.state[venue_id]
        state.last_trade = trade
        for builder in state.candles.values():
            builder.on_trade(trade)
        self._notify(venue_id, "candles")

    def _conn(self, venue_id: VenueId, conn: ConnState) -> None:
        state = self.state[venue_id]
        state.conn, state.conn_at = conn, self._clock()
        self._notify(venue_id, "conn")


class RemoteAccount:
    """The daemon's accounts with the `AccountService` face."""

    def __init__(self) -> None:
        self.state: dict[VenueId, VenueAccount] = {}
        self.faults = Faults()  # empty: faults are the daemon's and arrive as notices
        self._listeners: list[Callable[[VenueId, str], None]] = []

    def subscribe(self, listener: Callable[[VenueId, str], None]) -> None:
        self._listeners.append(listener)

    def subscribe_events(self, listener: Callable[..., None]) -> None:
        pass  # the daemon follows the fills

    def _update(self, accounts: dict[VenueId, VenueAccount]) -> None:
        self.state = accounts
        for venue_id in accounts:
            for what in ("conn", "balances", "orders"):
                self._notify(venue_id, what)

    def _notify(self, venue_id: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(venue_id, what)


EMPTY_REPORT = Report((), Summary(0, ZERO, None, None, None, 0, 0, 0, 0), ())


class RemoteResults:
    """The daemon's results (RF-25): it computes them with its journal and the token
    price. `refresh` asks; `load` gives the last one of each range."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link
        self._reports: dict[Range, Report] = {}
        self.error: str | None = None

    def load(self, rng: Range) -> Report:
        return self._reports.get(rng, EMPTY_REPORT)

    async def refresh(self, rng: Range) -> str | None:
        """Ask for the range's report; return the error, if any."""
        try:
            _, raw, _ = await self._link.request("results", {"range": rng})
        except (DaemonGoneError, DaemonRefusedError) as error:
            self.error = str(error)
            return self.error
        self._reports[rng] = decode(Report, raw)
        self.error = None
        return None


class RemoteDeposits:
    """The deposit addresses the daemon asks for (RF-28)."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link

    @property
    def venues(self) -> tuple[VenueId, ...]:
        snap = self._link.snapshot
        return tuple(snap.deposit_venues) if snap is not None else ()

    async def load(self) -> list[DepositRow]:
        try:
            _, raw, _ = await self._link.request("deposits", {})
        # each row says so, like a venue failure
        except (DaemonGoneError, DaemonRefusedError) as error:
            return [
                DepositRow(venue_id, a, None, str(error))
                for venue_id in self.venues
                for a in ASSETS
            ]
        return list(decode(Rows, raw).rows)


class RemoteRebalance:
    """The withdrawal fees the daemon asks for (RF-29)."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link

    @property
    def venues(self) -> tuple[VenueId, ...]:
        snap = self._link.snapshot
        return tuple(snap.fee_venues) if snap is not None else ()

    async def load(self) -> dict[tuple[VenueId, str], FeeInfo]:
        try:
            _, raw, _ = await self._link.request("withdrawal_fees", {})
        except (DaemonGoneError, DaemonRefusedError) as error:
            return {(venue_id, a): str(error) for venue_id in self.venues for a in ASSETS}
        return {(r.venue, r.asset): r.fee for r in decode(Fees, raw).fees}


class RemoteBot:
    """The daemon's bot with the `BotService` face."""

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
        """Sends `bot_on`; if the daemon refuses, it arrives as a notice."""
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
    """The daemon's orders with the `OrderService` face the app uses. The proposal is kept
    with its reference until confirmed; a daemon rejection on confirming (expired
    proposal…) comes back as the error result the app already knows how to show.
    Without a daemon, `DaemonGoneError`."""

    def __init__(self, link: RemoteEngine) -> None:
        self._link = link
        self._tokens: dict[int, tuple[object, str]] = {}  # id(proposal) → (proposal, token)
        self._listeners: list[Callable[[str], None]] = []

    # -- what the app reads ---------------------------------------------------------

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

    # -- requests ------------------------------------------------------------------

    async def _ask(self, command: str, args: dict[str, Any]) -> Any:
        kind, raw, token = await self._link.request(command, args)
        value = decode(REPLIES[kind], raw)
        if token is not None:
            self._tokens[id(value)] = (value, token)
        return value

    def _token(self, proposal: object) -> str:
        entry = self._tokens.pop(id(proposal), None)
        if entry is None or entry[0] is not proposal:
            raise DaemonRefusedError("esa propuesta no salió de este cliente")
        return entry[1]

    async def prepare(self, command: PlaceOrder) -> Pending | Rejected:
        return await self._ask("prepare", {"order": command})  # type: ignore[no-any-return]

    async def confirm(self, pending: Pending) -> Sent | Failed | Uncertain | Rejected:
        try:
            return await self._ask("confirm", {"token": self._token(pending)})  # type: ignore[no-any-return]
        except DaemonRefusedError as error:
            return Failed(pending.req, f"el daemon no la envió: {error}", local=True)

    async def prepare_double(self, command: DoubleOrder) -> PendingDouble | DoubleRejected:
        return await self._ask("prepare_double", {"order": command})  # type: ignore[no-any-return]

    async def confirm_double(self, pending: PendingDouble) -> DoubleSent | DoubleRejected:
        try:
            return await self._ask("confirm_double", {"token": self._token(pending)})  # type: ignore[no-any-return]
        except DaemonRefusedError as error:
            why = (Violation("daemon", f"el daemon no la envió: {error}"),)
            return DoubleRejected(pending.id, (Rejected(pending.buy.req, why),))

    async def plan_cancel(
        self, command: CancelOrder | CancelVenue | CancelAll
    ) -> CancelPlan | CancelRefused:
        args = {"kind": type(command).__name__, "value": command}
        return await self._ask("plan_cancel", args)  # type: ignore[no-any-return]

    async def execute_cancel(self, plan: CancelPlan) -> CancelResult:
        try:
            return await self._ask("execute_cancel", {"token": self._token(plan)})  # type: ignore[no-any-return]
        except DaemonRefusedError as exc:
            error = f"el daemon no canceló: {exc}"
            return CancelResult(
                {venue_id: len(o) for venue_id, o in plan.orders.items()},
                {venue_id: error for venue_id in plan.orders},
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
        # what to do with each daemon message type (`_handle`)
        self._handlers: dict[Any, Callable[[dict[str, Any]], None]] = {
            "hello": self._on_hello,
            "state": self._on_state,
            "market_init": self._on_market,
            "book": self._on_market,
            "trade": self._on_market,
            "conn": self._on_market,
            "fill": self._on_fill,
            "notice": self._on_notice,
            "fault": self._on_fault,
            "reply": self._on_reply,
            "error": self._on_error,
        }

    # -- what the app reads -------------------------------------------------------

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
        """Faults of the daemon session."""
        return self.snapshot.faults if self.snapshot is not None else 0

    @property
    def can_trade(self) -> dict[VenueId, bool]:
        return self.snapshot.can_trade if self.snapshot is not None else {}

    def subscribe(self, listener: Callable[[Notice], None]) -> None:
        self._listeners.append(listener)

    def subscribe_link(self, listener: Callable[[bool], None]) -> None:
        """Connected (True) or lost the daemon (False)."""
        self._link_listeners.append(listener)

    def _say(self, text: str, *, error: bool = False) -> None:
        notice = Notice(text, error)
        for listener in self._listeners:
            listener(notice)

    # -- connection ------------------------------------------------------------------

    def send(self, command: str) -> None:
        if self._writer is None or not self.connected:
            self._say(f"sin conexión con el daemon: {command} no se mandó", error=True)
            return
        self._writer.write(dumps({"cmd": command}))

    async def request(self, command: str, args: dict[str, Any]) -> tuple[str, Any, str | None]:
        """Send a request and wait for its reply: `(kind, undecoded value, token)`. Daemon
        error → `DaemonRefusedError`; no daemon → `DaemonGoneError`."""
        if self._writer is None or not self.connected:
            raise DaemonGoneError("sin conexión con el daemon: no se mandó nada")
        rid = next(self._ids)
        future: asyncio.Future[tuple[str, Any, str | None]] = (
            asyncio.get_running_loop().create_future()
        )
        self._waiting[rid] = future
        try:
            self._writer.write(dumps({"id": rid, "cmd": command, "args": args}))
            return await future
        finally:
            self._waiting.pop(rid, None)

    def _answered(self, msg: dict[str, Any]) -> bool:
        """Reply to a `request` request: True if it was one."""
        future = self._waiting.get(msg.get("id"))  # type: ignore[arg-type]
        if future is None:
            return False
        if not future.done():
            if msg.get("error") is not None:
                future.set_exception(DaemonRefusedError(str(msg["error"])))
            else:
                future.set_result((str(msg["kind"]), msg.get("value"), msg.get("token")))
        return True

    def _abandon(self) -> None:
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(DaemonGoneError("se perdió la conexión con el daemon"))
        self._waiting.clear()

    async def run(self) -> None:
        """Connect, read and, if the daemon leaves, retry until cancelled."""
        while True:
            try:
                reader, self._writer = await _open(self.path)
            except DaemonGoneError:
                await asyncio.sleep(self._retry_s)
                continue
            try:
                await self._read(reader)
            except (ConnectionError, OSError, ValueError) as exc:
                log.warning("connection with the daemon lost: %s", exc)
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
            except (KeyError, TypeError, ValueError) as error:  # an odd one does not cut the rest
                log.warning("unintelligible daemon message (%s): %s", msg.get("type"), error)

    def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind in ("reply", "error") and self._answered(msg):
            return
        handler = self._handlers.get(kind)  # a new daemon message type breaks nothing
        if handler is not None:
            handler(msg)

    def _on_hello(self, msg: dict[str, Any]) -> None:
        mode = str(msg["mode"])
        if self.mode is not None and mode != self.mode:
            self._say(f"⚠ el daemon ahora corre en {mode} (antes {self.mode})", error=True)
        self.mode = mode
        if not self.connected:
            self._set_link(True)

    def _on_state(self, msg: dict[str, Any]) -> None:
        before = self.doubles
        self.snapshot = decode(Snapshot, msg["state"])
        self.orders._changed(before, self.snapshot.doubles)
        self.account._update(self.snapshot.accounts)
        if self.bot is not None:
            self.bot._update(self.snapshot)

    def _on_market(self, msg: dict[str, Any]) -> None:
        self._market(msg["type"], decode(VenueId, msg["venue"]), msg)

    def _on_fill(self, msg: dict[str, Any]) -> None:
        self.account._notify(decode(VenueId, msg["venue"]), "fill")

    def _on_notice(self, msg: dict[str, Any]) -> None:
        self._say(str(msg["text"]), error=bool(msg["error"]))

    def _on_fault(self, msg: dict[str, Any]) -> None:
        self._say(f"daemon: {msg['message']}", error=True)

    def _on_reply(self, msg: dict[str, Any]) -> None:
        """A reply nobody awaited: it only counts if it brings an error."""
        if msg.get("error"):
            self._say(str(msg["error"]), error=True)

    def _on_error(self, msg: dict[str, Any]) -> None:
        self._say(f"el daemon no aceptó la orden: {msg['error']}", error=True)

    def _market(self, kind: str, venue_id: VenueId, msg: dict[str, Any]) -> None:
        if kind == "market_init":
            self.market._init(venue_id, decode(MarketView, msg["market"]))
        elif kind == "book":
            self.market._book(venue_id, decode(BookSnapshot | None, msg["book"]))
        elif kind == "trade":
            self.market._trade(venue_id, decode(Trade, msg["trade"]))
        else:
            self.market._conn(venue_id, decode(ConnState, msg["conn"]))

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
