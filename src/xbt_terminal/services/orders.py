"""OrderService: simple and double orders, confirmation, journal.

Simple order flow (design.md §6.2):

    prepare(cmd) ─ RiskCheck ─┬─ rejected → journal reject_local → Rejected
                              └─ ok → Pending (notional and fee, for the dialog)
    confirm(pending) ─ RiskCheck again ─┬─ rejected → reject_local
                                        └─ order_sent → place_limit → order_ack | order_error

With the quantity in % (RF-17, D-21), `prepare` computes it first with the same
balances and the dialog says which balance it comes from.

It validates again on confirming because the market can move with the dialog open:
when in doubt, it does not send. Balances and orders come from `AccountService` if
younger than `CACHE_MAX_AGE_MS`, plus those just sent the account has not seen yet;
otherwise they are asked of the venue (RNF-11, D-18). The book is always the live
one. A venue without keys is not in `venues` and is rejected as `read_only`. Book
freshness for `stale_data` is the latest of its last message's arrival and the last
heartbeat of an "up" connection (D-13), both on the local clock (RNF-16). In live, a
venue skewed more than `max_clock_skew_s`, or with no measured skew, is rejected as
`clock_skew` (D-30).

Double order (design.md §6.4, D-15): both legs pass RiskCheck on preparing and
confirming; if one fails neither is sent. One confirmation with both notionals and
the gross and net spread, sending with `asyncio.gather`, and each leg is followed
with the orders arriving from the account (`on_private_event`). `check_doubles`
(every second) writes `hanging_leg` once per double order. Everything a double order
writes to the journal has `double` in `data`.

Bot double with `lead_venue` (D-75): `confirm_double(pending, lead=...)` validates both
legs but sends only that venue's; the other stays `held`. The bot sends it later with
`send_held` for what the first filled, or leaves it `skipped` with `skip_held`. The
`held` one has no `order_id`: it is neither cancelled nor reconciled.

Uncertain send (RNF-12, D-26): if the venue raises `UncertainError`, the result is
`Uncertain`, the journal says `order_unknown` and the order stays in `uncertain` (in a
double, the `unknown` leg). `reconcile` (the UI calls it every few seconds while any
exists) looks for the order in the venue's `recent_orders` with `core/reconcile.py`:
if it shows up, `order_ack` with `reconciled`; with no trace in 60 s, `order_error`
with `reconciled`; with several candidates it stays uncertain.

Atomic send per venue (RNF-13, D-27): one `asyncio.Lock` per venue covers revalidate →
send → note the order (in `_just_sent` or `uncertain`), so another order of the same
venue validates counting the one in flight. A double takes both locks, always in the
same order. Uncertain ones count as open when validating until resolved.

Failing journal (RNF-14, D-28): without `order_sent` nothing is sent (local `Failed`).
Any other failed write (disk full after sending) breaks nothing: the order counts
anyway and the fault goes to `faults`, shown in red.

Safe exit (RNF-15, D-29): each send and cancel runs in its own task, protected with
`asyncio.shield`: if its waiter is cancelled (closing the app), the request finishes
and lands in the journal. `drain` waits for those in flight; the app calls it before
closing the journal.

Cancelling (RF-08, T-19): `plan_cancel` says what will be cancelled, to confirm, and
`execute_cancel` does it: one order with `cancel`, a venue or all with their
`cancel_all` (which on the venue cancels all of the pair, also those not sent from the
terminal). Then those still open are counted: CA-05 asks for zero. Journal:
`cancel_sent`, `cancel_ack` with those left, or `error`.

Spec: RF-06, RF-07, RF-10, RF-11, RF-13, RF-17, RNF-12, RNF-13, RNF-14
RNF-15 · design.md §6.2-6.4 · Tasks: T-16, T-18, T-23, T-25 to T-28
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, TypeVar

import httpx

from ..adapters.base import PrivateEvent, UncertainError, Venue, VenueError, describe
from ..adapters.streaming import NOT_SENT, now_ms
from ..core.commands import CancelAll, CancelOrder, CancelVenue, DoubleOrder, Percent, PlaceOrder
from ..core.double import DoubleState, hanging, on_order, start
from ..core.inventory import inventory
from ..core.journal import Journal
from ..core.models import ZERO, Balance, Order, OrderRequest, VenueId, asset_label
from ..core.proposals import (  # re-exported: the public face of orders
    NAMES,
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
from ..core.reconcile import WINDOW_MS, candidates
from ..core.risk import RiskContext, RiskLimits, Violation, check
from ..core.sizing import size_from_percent
from .account import AccountService
from .faults import Faults
from .market import MarketHub

CACHE_MAX_AGE_MS = 30_000  # account balances valid for validating (RNF-11)
GIVE_UP_MS = WINDOW_MS  # no trace of an uncertain order in this time: it does not exist (D-26)

T = TypeVar("T")


def new_client_id() -> str:
    return f"t-{uuid.uuid4().hex[:6]}"


def new_double_id() -> str:
    return f"d-{uuid.uuid4().hex[:6]}"


@dataclass(slots=True)
class UncertainOrder:
    req: OrderRequest
    sent_at: int
    known: frozenset[str]  # venue orders known at send time: not candidates
    extra: dict[str, Any] = field(default_factory=dict)
    warned: bool = False  # already warned of several candidates


@dataclass(frozen=True, slots=True)
class Reconciled:
    """What reconciliation found about an uncertain order, to notify."""

    client_id: str
    venue: VenueId
    outcome: str  # found | missing | ambiguous
    message: str

    @property
    def error(self) -> bool:
        return self.outcome != "found"


class OrderService:
    def __init__(
        self,
        hub: MarketHub,
        venues: Mapping[VenueId, Venue],
        journal: Journal,
        limits: RiskLimits,
        *,
        clock: Callable[[], int] = now_ms,
        new_client_id: Callable[[], str] = new_client_id,
        new_double_id: Callable[[], str] = new_double_id,
        account: AccountService | None = None,
        faults: Faults | None = None,
        live: bool = False,
    ) -> None:
        self._hub = hub
        self._live = live  # clock skew only blocks real orders (D-30)
        self._venues = dict(venues)  # only those able to trade
        self._journal = journal
        self.faults = faults or Faults(journal, clock=clock)
        self._limits = limits
        self._clock = clock
        self._new_client_id = new_client_id
        self._new_double_id = new_double_id
        self._account = account
        self._just_sent: dict[VenueId, dict[str, Order]] = {}  # not yet seen on the account
        self.uncertain: dict[str, UncertainOrder] = {}  # client_id → uncertain send
        self._acked: dict[VenueId, set[str]] = {}  # orders the venue confirmed to the terminal
        self._locks: dict[VenueId, asyncio.Lock] = {}  # validate → send, one per venue
        self._inflight: set[asyncio.Task[Any]] = set()  # sends and cancels (RNF-15)
        self.doubles: dict[str, DoubleState] = {}
        self.hanging: dict[str, str] = {}  # id → reason, once warned
        self._double_listeners: list[Callable[[str], None]] = []

    def close(self) -> None:
        self._journal.close()

    def _record(self, event: str, *, note: str | None = None, **kw: Any) -> None:
        """Write to the journal; on failure, report and go on (D-28)."""
        try:
            self._journal.write(event, **kw)
        except OSError as error:
            note = note or f"{event} sin escribir en el journal"
            self.faults.report("journal", error, venue=kw.get("venue"), note=note)

    def _protected(self, work: Coroutine[Any, Any, T]) -> Awaitable[T]:
        """What goes out to the venue finishes even if its waiter is cancelled."""
        task = asyncio.ensure_future(work)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return asyncio.shield(task)

    @property
    def in_flight(self) -> int:
        return len(self._inflight)

    async def drain(self) -> None:
        """Wait for sends and cancels in flight (before exiting)."""
        while self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    def can_trade(self, venue: VenueId) -> bool:
        return venue in self._venues

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    async def prepare(self, command: PlaceOrder) -> Pending | Rejected:
        client_id = self._new_client_id()
        if not isinstance(command.qty, Percent):
            order_request = OrderRequest(
                command.venue, command.side, command.qty, command.price, client_id
            )
            return await self._validate(order_request)
        sized = await self._size(command, command.qty, client_id)
        if isinstance(sized, Rejected):
            return sized
        order_request, basis = sized
        return await self._validate(order_request, basis=basis)

    async def _size(
        self, command: PlaceOrder, percent: Percent, client_id: str
    ) -> tuple[OrderRequest, str] | Rejected:
        """Quantity from the % of the venue balance (RF-17, D-21), with the same balances
        validated against. Without enough for a lot, reject."""
        order_request = OrderRequest(command.venue, command.side, ZERO, command.price, client_id)
        if bad := self._unavailable(order_request):
            return self._reject(order_request, bad)
        venue, spec = self._venues[command.venue], self._hub.state[command.venue].spec
        assert spec is not None  # _unavailable checks it
        try:
            _, balances = await self._snapshot(command.venue, venue)
        except (VenueError, httpx.HTTPError) as error:
            return self._reject(order_request, [self._unreadable(command.venue, error)])
        s = size_from_percent(command.side, percent.value, command.price, balances, spec)
        basis = f"{s.pct} % de {s.basis:.8f} {asset_label(s.asset)} en {NAMES[command.venue]}"
        order_request = OrderRequest(command.venue, command.side, s.qty, command.price, client_id)
        if s.qty <= 0:
            msg = f"{basis}: no llega a un lote ({spec.lot_size:f} {asset_label(spec.base)})"
            return self._reject(order_request, [Violation("insufficient_balance", msg)])
        return order_request, basis

    @asynccontextmanager
    async def _sending(self, *venues: VenueId) -> AsyncIterator[None]:
        """The venues' locks, in fixed order so two doubles do not deadlock."""
        async with AsyncExitStack() as stack:
            for venue_id in sorted(set(venues)):
                await stack.enter_async_context(self._locks.setdefault(venue_id, asyncio.Lock()))
            yield

    async def confirm(self, pending: Pending) -> Sent | Failed | Uncertain | Rejected:
        async with self._sending(pending.req.venue):
            checked = await self._validate(pending.req)
            if isinstance(checked, Rejected):
                return checked
            return await self._send(pending.req, {})

    async def _send(
        self, order_request: OrderRequest, extra: dict[str, Any]
    ) -> Sent | Failed | Uncertain:
        return await self._protected(self._send_now(order_request, extra))

    async def _send_now(
        self, order_request: OrderRequest, extra: dict[str, Any]
    ) -> Sent | Failed | Uncertain:
        known, sent_at = self._known(order_request.venue), self._clock()
        try:
            self._journal.write(
                "order_sent",
                venue=order_request.venue,
                client_id=order_request.client_id,
                data={
                    "side": order_request.side,
                    "qty": order_request.qty,
                    "price": order_request.price,
                    **extra,
                },
            )
        except OSError as exception:
            return Failed(
                order_request,
                f"no se pudo escribir el journal ({exception}); no se envió",
                local=True,
            )
        try:
            order = await self._venues[order_request.venue].place_limit(order_request)
        except UncertainError as exception:
            self._record(
                "order_unknown",
                venue=order_request.venue,
                client_id=order_request.client_id,
                data={"error": describe(exception), **extra},
            )
            self.uncertain[order_request.client_id] = UncertainOrder(
                order_request, sent_at, known, extra
            )
            return Uncertain(order_request, describe(exception))
        except (VenueError, httpx.HTTPError) as exception:
            # unconnected, the order never left: it must not look like a venue rejection (D-51)
            error = describe(exception)
            if isinstance(exception, NOT_SENT):
                error = f"no enviada, sin conexión ({error})"
            self._record(
                "order_error", venue=order_request.venue, client_id=order_request.client_id,
                data={"error": error, **extra},
            )  # fmt: skip
            return Failed(order_request, error)
        self._record(
            "order_ack",
            note=f"orden {order.order_id} enviada, pero sin order_ack en el journal",
            venue=order_request.venue,
            client_id=order_request.client_id,
            data={"order_id": order.order_id, "status": order.status, **extra},
        )
        self._acked.setdefault(order_request.venue, set()).add(order.order_id)
        if not order.is_done:
            self._just_sent.setdefault(order_request.venue, {})[order.order_id] = order
        return Sent(order)

    # -- double order ------------------------------------------------------------

    def subscribe_doubles(self, listener: Callable[[str], None]) -> None:
        self._double_listeners.append(listener)

    def _changed(self, double_id: str) -> None:
        for listener in self._double_listeners:
            listener(double_id)

    async def prepare_double(self, command: DoubleOrder) -> PendingDouble | DoubleRejected:
        double_id = self._new_double_id()
        buy = OrderRequest(
            command.buy_venue, "buy", command.qty, command.buy_price, self._new_client_id()
        )
        sell = OrderRequest(
            command.sell_venue, "sell", command.qty, command.sell_price, self._new_client_id()
        )
        return await self._validate_double(double_id, buy, sell)

    async def _validate_double(
        self, double_id: str, buy: OrderRequest, sell: OrderRequest
    ) -> PendingDouble | DoubleRejected:
        extra = {"double": double_id}
        checked = [await self._validate(buy, extra), await self._validate(sell, extra)]
        rejected = tuple(c for c in checked if isinstance(c, Rejected))
        if rejected:
            return DoubleRejected(double_id, rejected)
        b, s = checked
        assert isinstance(b, Pending) and isinstance(s, Pending)
        return PendingDouble(double_id, b, s)

    async def confirm_double(
        self, pending: PendingDouble, *, lead: VenueId | None = None
    ) -> DoubleSent | DoubleRejected:
        """With `lead` (the bot, D-75), only that venue's leg goes; the other stays `held`
        until `send_held` or `skip_held`."""
        buy, sell = pending.buy.req, pending.sell.req
        async with self._sending(buy.venue, sell.venue):
            checked = await self._validate_double(pending.id, buy, sell)
            if isinstance(checked, DoubleRejected):
                return checked
            extra = {"double": pending.id}
            if lead is None:
                self.doubles[pending.id] = start(pending.id, buy, sell, self._clock())
                results = await asyncio.gather(self._send(buy, extra), self._send(sell, extra))
            else:
                first, held = (buy, sell) if buy.venue == lead else (sell, buy)
                self.doubles[pending.id] = start(
                    pending.id, buy, sell, self._clock(), held=held.side
                )
                results = [await self._send(first, extra)]
        for result in results:
            self._apply(pending.id, result)
        self._changed(pending.id)
        return DoubleSent(self.doubles[pending.id])

    async def send_held(self, double_id: str, quantity: Decimal) -> Sent | Failed | Uncertain:
        """Send the double's `held` leg for `qty` (D-75). It was validated with the first on
        confirming; `qty` does not exceed what was validated."""
        state = self.doubles[double_id]
        (side,) = (order_leg.req.side for order_leg in state.legs if order_leg.status == "held")
        state = state.resize(side, quantity)
        self.doubles[double_id] = state
        order_request = state.leg(side).req
        async with self._sending(order_request.venue):
            result = await self._send(order_request, {"double": double_id})
        self._apply(double_id, result)
        self._changed(double_id)
        return result

    def skip_held(self, double_id: str) -> None:
        """The `held` leg is not sent (D-75): counts as closed with 0 filled."""
        state = self.doubles[double_id]
        for order_leg in state.legs:
            if order_leg.status == "held":
                state = state.skip(order_leg.req.side)
        self.doubles[double_id] = state
        self._changed(double_id)

    def _apply(self, double_id: str, result: Sent | Failed | Uncertain) -> None:
        state = self.doubles[double_id]
        if isinstance(result, Sent):
            state = on_order(state, result.order, self._clock())
        elif isinstance(result, Uncertain):
            state = state.uncertain(result.req.side, result.error)
        else:
            state = state.fail(result.req.side, result.error)
        self.doubles[double_id] = state

    def on_private_event(self, venue: VenueId, event: PrivateEvent) -> None:
        """Account orders (AccountService): they update the legs, and an order just sent
        stops being counted apart once the account sees it."""
        if not isinstance(event, Order):
            return
        self._just_sent.get(venue, {}).pop(event.order_id, None)
        self._update_doubles(event)

    def _update_doubles(self, order: Order) -> None:
        now = self._clock()
        for double_id, state in list(self.doubles.items()):
            updated = on_order(state, order, now)
            if updated != state:
                self.doubles[double_id] = updated
                self._changed(double_id)

    def check_doubles(self) -> list[str]:
        """Ids just left with a hanging leg; writes `hanging_leg` once."""
        now = self._clock()
        after_ms = self._limits.hanging_leg_after_s * 1000
        new: list[str] = []
        for double_id, state in self.doubles.items():
            if double_id in self.hanging:
                continue
            reason = hanging(state, now, after_ms)
            if reason is None:
                continue
            self.hanging[double_id] = reason
            self._record(
                "hanging_leg",
                data={
                    "double": double_id,
                    "reason": reason,
                    "legs": {
                        order_leg.req.side: {
                            "venue": order_leg.req.venue,
                            "client_id": order_leg.req.client_id,
                            "order_id": order_leg.order_id,
                            "status": order_leg.status,
                            "qty": order_leg.req.qty,
                            "filled": order_leg.filled,
                        }
                        for order_leg in state.legs
                    },
                },
            )
            new.append(double_id)
            self._changed(double_id)
        return new

    # -- uncertain sends (RNF-12, D-26) ------------------------------------------

    def _known(self, venue_id: VenueId) -> frozenset[str]:
        """Venue orders already known: the account's open ones and those just sent."""
        state = self._account.state.get(venue_id) if self._account is not None else None
        ids = set(state.orders) if state is not None else set()
        return frozenset(ids | set(self._just_sent.get(venue_id, {})))

    async def reconcile(self) -> list[Reconciled]:
        """One round: each venue with uncertain orders is queried once. If it does not
        answer, it waits for the next (without looking, nothing is deemed nonexistent)."""
        by_venue: dict[VenueId, list[UncertainOrder]] = {}
        for u in self.uncertain.values():
            by_venue.setdefault(u.req.venue, []).append(u)
        out: list[Reconciled] = []
        for venue_id, pending in by_venue.items():
            venue = self._venues[venue_id]
            since = min(u.sent_at for u in pending) - WINDOW_MS
            try:
                orders = await venue.recent_orders(since)
            except (VenueError, httpx.HTTPError):
                continue
            now = self._clock()
            for u in pending:
                # neither those the terminal sent later with an ack: they are others
                known = u.known | self._acked.get(venue_id, set())
                by_id = venue.keeps_client_id
                found = candidates(u.req, orders, u.sent_at, known, by_client_id=by_id)
                if len(found) == 1:
                    out.append(self._found(u, found[0]))
                elif found and not u.warned:
                    u.warned = True
                    msg = (
                        f"orden incierta {u.req.client_id}: {len(found)} órdenes iguales en "
                        f"{NAMES[venue_id]} ({', '.join(o.order_id for o in found)}); "
                        "sigue incierta, "
                        "revísala a mano"
                    )
                    out.append(Reconciled(u.req.client_id, venue_id, "ambiguous", msg))
                elif not found and now - u.sent_at > GIVE_UP_MS:
                    out.append(self._missing(u))
        return out

    def _found(self, u: UncertainOrder, order: Order) -> Reconciled:
        order_request = u.req
        del self.uncertain[order_request.client_id]
        order = replace(
            order, client_id=order_request.client_id
        )  # Neoxa does not return it (PA-09)
        self._record(
            "order_ack",
            venue=order_request.venue,
            client_id=order_request.client_id,
            data={
                "order_id": order.order_id,
                "status": order.status,
                "reconciled": True,
                **u.extra,
            },
        )
        self._acked.setdefault(order_request.venue, set()).add(order.order_id)
        if not order.is_done:
            self._just_sent.setdefault(order_request.venue, {})[order.order_id] = order
        self._update_doubles(order)
        msg = (
            f"orden incierta {order_request.client_id}: existe en "
            f"{NAMES[order_request.venue]} como "
            f"{order.order_id} ({order.status})"
        )
        return Reconciled(order_request.client_id, order_request.venue, "found", msg)

    def _missing(self, u: UncertainOrder) -> Reconciled:
        order_request = u.req
        del self.uncertain[order_request.client_id]
        error = f"no existe en {NAMES[order_request.venue]} tras {GIVE_UP_MS // 1000} s"
        self._record(
            "order_error",
            venue=order_request.venue,
            client_id=order_request.client_id,
            data={"error": error, "reconciled": True, **u.extra},
        )
        for double_id, state in list(self.doubles.items()):
            order_leg = state.leg(order_request.side)
            if order_leg.req.client_id == order_request.client_id and order_leg.status == "unknown":
                self.doubles[double_id] = state.fail(order_request.side, error)
                self._changed(double_id)
        msg = f"orden incierta {order_request.client_id}: {error}; no se creó"
        return Reconciled(order_request.client_id, order_request.venue, "missing", msg)

    # -- cancelling ---------------------------------------------------------------

    async def plan_cancel(
        self, command: CancelOrder | CancelVenue | CancelAll
    ) -> CancelPlan | CancelRefused:
        if isinstance(command, CancelVenue) and command.venue not in self._venues:
            return CancelRefused(f"{NAMES[command.venue]}: sin keys, solo lectura")
        venues = [command.venue] if isinstance(command, CancelVenue) else list(self._venues)
        try:
            found = {
                venue_id: tuple(await self._venues[venue_id].open_orders()) for venue_id in venues
            }
        except (VenueError, httpx.HTTPError) as error:
            return CancelRefused(f"no se pudieron leer las órdenes abiertas ({describe(error)})")
        if isinstance(command, CancelOrder):
            hits = {
                venue_id: tuple(o for o in orders if o.order_id == command.order_id)
                for venue_id, orders in found.items()
            }
            hits = {venue_id: o for venue_id, o in hits.items() if o}
            if not hits:
                return CancelRefused(f"no hay ninguna orden abierta {command.order_id}")
            if len(hits) > 1:
                return CancelRefused(
                    f"{command.order_id} está en los dos venues: usa cancel <venue>"
                )
            return CancelPlan("order", hits)
        found = {venue_id: orders for venue_id, orders in found.items() if orders}
        if not found:
            return CancelRefused("no hay órdenes abiertas que cancelar")
        return CancelPlan("venue" if isinstance(command, CancelVenue) else "all", found)

    async def cancel_double(self, double_id: str) -> CancelResult:
        """Cancel a double's legs still open on their venue (the bot, past `fill_timeout_ms`).
        Uncertain ones have no id: left alone."""
        state = self.doubles[double_id]
        orders: dict[VenueId, tuple[Order, ...]] = {}
        for order_leg in state.legs:
            if order_leg.done or order_leg.order_id is None or order_leg.status == "unknown":
                continue
            r = order_leg.req
            order = Order(
                r.venue,
                order_leg.order_id,
                r.client_id,
                r.side,
                r.price,
                r.qty,
                order_leg.filled,
                "open",
                self._clock(),
            )  # fmt: skip
            orders[r.venue] = (order,)
        if not orders:
            return CancelResult({}, {})
        return await self.execute_cancel(CancelPlan("order", orders))

    async def execute_cancel(self, plan: CancelPlan) -> CancelResult:
        """All venues at once; each with its result even if another fails."""
        results = await asyncio.gather(
            *(
                self._protected(self._cancel_in(venue_id, plan.scope, orders))
                for venue_id, orders in plan.orders.items()
            )
        )
        remaining = {venue_id: n for venue_id, n, _ in results}
        errors = {venue_id: err for venue_id, _, err in results if err}
        return CancelResult(remaining, errors)

    async def _cancel_in(
        self, venue_id: VenueId, scope: str, orders: tuple[Order, ...]
    ) -> tuple[VenueId, int, str | None]:
        venue = self._venues[venue_id]
        ids = [o.order_id for o in orders]
        client_id = orders[0].client_id if scope == "order" else None
        self._record(
            "cancel_sent",
            venue=venue_id,
            client_id=client_id,
            data={"scope": scope, "order_ids": ids},
        )
        try:
            if scope == "order":
                await venue.cancel(ids[0])
            else:
                await venue.cancel_all()
            still = await venue.open_orders()
            # one order: if it remains; venue or all: any of the pair (CA-05)
            left = sum(o.order_id in ids for o in still) if scope == "order" else len(still)
        except (VenueError, httpx.HTTPError) as error:
            self._record(
                "error",
                venue=venue_id,
                client_id=client_id,
                data={
                    "action": "cancel",
                    "scope": scope,
                    "order_ids": ids,
                    "error": describe(error),
                },
            )
            return venue_id, len(orders), describe(error)
        self._record(
            "cancel_ack",
            venue=venue_id,
            client_id=client_id,
            data={"scope": scope, "remaining": left},
        )
        return venue_id, left, None

    # -- validation -------------------------------------------------------------

    async def _validate(
        self,
        order_request: OrderRequest,
        extra: dict[str, Any] | None = None,
        *,
        basis: str | None = None,
    ) -> Pending | Rejected:
        violations = await self._violations(order_request)
        if violations:
            return self._reject(order_request, violations, extra)
        spec = self._hub.state[order_request.venue].spec
        assert spec is not None  # _violations checks it
        return Pending(
            order_request,
            spec,
            order_request.notional,
            order_request.notional * spec.taker_fee,
            basis,
        )

    def _reject(
        self,
        order_request: OrderRequest,
        violations: list[Violation],
        extra: dict[str, Any] | None = None,
    ) -> Rejected:
        self._record(
            "reject_local",
            venue=order_request.venue,
            client_id=order_request.client_id,
            data={
                "side": order_request.side,
                "qty": order_request.qty,
                "price": order_request.price,
                "violations": [{"code": v.code, "message": v.message} for v in violations],
                **(extra or {}),
            },
        )
        return Rejected(order_request, tuple(violations))

    def _unavailable(self, order_request: OrderRequest) -> list[Violation]:
        """Without keys or a loaded market there is nothing more to validate."""
        if order_request.venue not in self._venues:
            return [Violation("read_only", f"{NAMES[order_request.venue]}: sin keys, solo lectura")]
        if self._hub.state[order_request.venue].spec is None:
            return [
                Violation("stale_data", f"{NAMES[order_request.venue]}: mercado aún sin cargar")
            ]
        return []

    @staticmethod
    def _unreadable(venue_id: VenueId, error: Exception) -> Violation:
        # without balances or orders it cannot validate: when in doubt, do not send (D-16)
        msg = f"{NAMES[venue_id]}: no se pudieron leer saldos u órdenes ({describe(error)})"
        return Violation("venue_error", msg)

    async def _violations(self, order_request: OrderRequest) -> list[Violation]:
        if bad := self._unavailable(order_request):
            return bad
        state = self._hub.state[order_request.venue]
        assert state.spec is not None
        try:
            # all venues with keys: the per order cap is a % of the total (D-19)
            snaps = {
                venue_id: await self._snapshot(venue_id, v) for venue_id, v in self._venues.items()
            }
        except (VenueError, httpx.HTTPError) as error:
            return [self._unreadable(order_request.venue, error)]
        open_orders, balances = snaps[order_request.venue]
        total = inventory(
            {venue_id: bal for venue_id, (_, bal) in snaps.items()},
            {venue_id: self._hub.state[venue_id].book.mid for venue_id in snaps},
        )
        ctx = RiskContext(
            market=state.spec,
            best_bid=state.book.best_bid,
            best_ask=state.book.best_ask,
            book_ts=self.fresh_at(order_request.venue),
            now=self._clock(),
            open_orders=open_orders,
            balances=balances,
            inventory_value=total.value,
        )
        return check(order_request, ctx, self._limits) + self._skew(order_request.venue)

    def _skew(self, venue_id: VenueId) -> list[Violation]:
        if not self._live:
            return []
        skew, cap = self._hub.clock_skew_ms(venue_id), self._limits.max_clock_skew_s
        if skew is None:
            msg = f"{NAMES[venue_id]}: desfase de reloj sin medir; no se envía hasta saberlo"
            return [Violation("clock_skew", msg)]
        if abs(skew) > cap * 1000:
            msg = (
                f"{NAMES[venue_id]}: reloj desfasado {skew / 1000:+.0f} s (máx. {cap} s); "
                "sincroniza el reloj del sistema (NTP)"
            )
            return [Violation("clock_skew", msg)]
        return []

    async def _snapshot(
        self, venue_id: VenueId, venue: Venue
    ) -> tuple[tuple[Order, ...], dict[str, Balance]]:
        """Open orders and balances for RiskCheck: the account's if up to date, plus that
        venue's uncertain ones (they may exist, D-27)."""
        state = self._account.state.get(venue_id) if self._account is not None else None
        if (
            state is not None
            and state.balances is not None
            and state.balances_at is not None
            and self._clock() - state.balances_at <= CACHE_MAX_AGE_MS
        ):
            orders = dict(state.orders)
            for oid, order in self._just_sent.get(venue_id, {}).items():
                orders.setdefault(oid, order)
            balances = dict(state.balances)
        else:
            orders = {o.order_id: o for o in await venue.open_orders()}
            balances = await venue.balances()
        seen = {o.client_id for o in orders.values()}
        for u in self.uncertain.values():
            r = u.req
            if r.venue == venue_id and r.client_id not in seen:
                orders[f"?{r.client_id}"] = Order(
                    venue_id, f"?{r.client_id}", r.client_id, r.side, r.price, r.qty, ZERO, "open",
                    u.sent_at,
                )  # fmt: skip
        return tuple(orders.values()), balances

    def fresh_at(self, venue: VenueId) -> int | None:
        """Last moment the book is known to be current: its last message or the last REST
        check that matched. A live connection is no longer enough (D-76, fixes D-13); a
        book in doubt is not current."""
        state = self._hub.state[venue]
        if not state.book.valid or state.suspect:
            return None
        times = [t for t in (state.book.received_at, state.checked_at) if t is not None]
        return max(times) if times else None
