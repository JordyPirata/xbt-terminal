"""OrderService: orden simple y doble, confirmación, journal.

Flujo de la orden simple (design.md §6.2):

    prepare(cmd) ─ RiskCheck ─┬─ rechazo → journal reject_local → Rejected
                              └─ ok → Pending (nocional y fee, para el diálogo)
    confirm(pending) ─ RiskCheck otra vez ─┬─ rechazo → reject_local
                                           └─ order_sent → place_limit → order_ack | order_error

Con la cantidad en % (RF-17, D-21), `prepare` la calcula antes con los mismos
saldos y el diálogo dice de qué saldo sale.

Se valida otra vez al confirmar porque el mercado puede moverse con el diálogo
abierto: ante la duda, no se envía. Saldos y órdenes salen de `AccountService`
si tienen menos de `CACHE_MAX_AGE_MS`, más las recién enviadas que la cuenta aún
no ha visto; si no, se piden al venue (RNF-11, D-18). El book es siempre el
vivo. Un venue sin keys no está en `venues` y se rechaza como `read_only`. La
frescura del book para `stale_data` es la más reciente entre la llegada de su
último mensaje y el último latido de una conexión "up" (D-13), las dos con el
reloj local (RNF-16). En live, un venue con el reloj desfasado más de
`max_clock_skew_s`, o sin desfase medido, se rechaza como `clock_skew` (D-30).

Orden doble (design.md §6.4, D-15): las dos piernas pasan RiskCheck al preparar
y al confirmar; si una falla no se envía ninguna. Una sola confirmación con los
dos nocionales y el spread bruto y neto, envío con `asyncio.gather`, y cada
pierna se sigue con las órdenes que llegan de la cuenta (`on_private_event`).
`check_doubles` (cada segundo) escribe `hanging_leg` una vez por orden doble.
Todo lo que escribe al journal una orden doble lleva `double` en `data`.

Doble del bot con `lead_venue` (D-75): `confirm_double(pending, lead=...)` valida
las dos piernas pero envía solo la de ese venue; la otra queda `held`. El bot la
envía después con `send_held` por lo que llenó la primera, o la deja `skipped`
con `skip_held`. La `held` no tiene `order_id`: no se cancela ni se reconcilia.

Envío incierto (RNF-12, D-26): si el venue lanza `UncertainError`, el resultado
es `Uncertain`, el journal dice `order_unknown` y la orden queda en `uncertain`
(en una orden doble, la pierna `unknown`). `reconcile` (la UI lo llama cada pocos
segundos mientras haya alguna) busca la orden en `recent_orders` del venue con
`core/reconcile.py`: si aparece, `order_ack` con `reconciled`; si en 60 s no hay
rastro, `order_error` con `reconciled`; con varias candidatas sigue incierta.

Envío atómico por venue (RNF-13, D-27): un `asyncio.Lock` por venue cubre
revalidar → enviar → apuntar la orden (en `_just_sent` o en `uncertain`), así
que otra orden del mismo venue se valida contando la que está en vuelo. La orden
doble toma los dos locks, siempre en el mismo orden. Las inciertas cuentan como
abiertas al validar mientras no se resuelvan.

Journal que falla (RNF-14, D-28): sin `order_sent` no se envía (`Failed` local).
Cualquier otra escritura que falle (el disco lleno tras enviar) no rompe nada:
la orden cuenta igual y el fallo va a `faults`, que lo enseña en rojo.

Salida segura (RNF-15, D-29): cada envío y cada cancelación corre en su propia
tarea, protegida con `asyncio.shield`: si se cancela a quien la espera (al
cerrar la app), la petición termina y queda en el journal. `drain` espera a las
que estén en vuelo; la app lo llama antes de cerrar el journal.

Cancelar (RF-08, T-19): `plan_cancel` dice qué se va a cancelar, para confirmar,
y `execute_cancel` lo hace: una orden con `cancel`, un venue o todos con su
`cancel_all` (que en el venue cancela todas las del par, también las que no se
enviaron desde la terminal). Después se cuentan las que siguen abiertas: CA-05
pide cero. Journal: `cancel_sent`, `cancel_ack` con las que quedan, o `error`.

Spec: RF-06, RF-07, RF-10, RF-11, RF-13, RF-17, RNF-12, RNF-13, RNF-14
RNF-15 · design.md §6.2-6.4 · Tareas: T-16, T-18, T-23, T-25 a T-28
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
from ..core.proposals import (  # re-exportados: la cara pública de las órdenes
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

CACHE_MAX_AGE_MS = 30_000  # saldos de la cuenta válidos para validar (RNF-11)
GIVE_UP_MS = WINDOW_MS  # sin rastro de una orden incierta en este tiempo: no existe (D-26)

T = TypeVar("T")


def new_client_id() -> str:
    return f"t-{uuid.uuid4().hex[:6]}"


def new_double_id() -> str:
    return f"d-{uuid.uuid4().hex[:6]}"


@dataclass(slots=True)
class UncertainOrder:
    req: OrderRequest
    sent_at: int
    known: frozenset[str]  # órdenes del venue ya conocidas al enviar: no son candidatas
    extra: dict[str, Any] = field(default_factory=dict)
    warned: bool = False  # ya se avisó de que hay varias candidatas


@dataclass(frozen=True, slots=True)
class Reconciled:
    """Lo que la reconciliación averiguó de una orden incierta, para avisar."""

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
        self._live = live  # el desfase de reloj solo bloquea órdenes reales (D-30)
        self._venues = dict(venues)  # solo los que pueden operar
        self._journal = journal
        self.faults = faults or Faults(journal, clock=clock)
        self._limits = limits
        self._clock = clock
        self._new_client_id = new_client_id
        self._new_double_id = new_double_id
        self._account = account
        self._just_sent: dict[VenueId, dict[str, Order]] = {}  # aún sin ver en la cuenta
        self.uncertain: dict[str, UncertainOrder] = {}  # client_id → envío incierto
        self._acked: dict[VenueId, set[str]] = {}  # órdenes que el venue confirmó a la terminal
        self._locks: dict[VenueId, asyncio.Lock] = {}  # validar → enviar, uno por venue
        self._inflight: set[asyncio.Task[Any]] = set()  # envíos y cancelaciones (RNF-15)
        self.doubles: dict[str, DoubleState] = {}
        self.hanging: dict[str, str] = {}  # id → motivo, una vez avisada
        self._double_listeners: list[Callable[[str], None]] = []

    def close(self) -> None:
        self._journal.close()

    def _record(self, event: str, *, note: str | None = None, **kw: Any) -> None:
        """Escribe en el journal; si falla, lo reporta y sigue (D-28)."""
        try:
            self._journal.write(event, **kw)
        except OSError as exc:
            note = note or f"{event} sin escribir en el journal"
            self.faults.report("journal", exc, venue=kw.get("venue"), note=note)

    def _protected(self, work: Coroutine[Any, Any, T]) -> Awaitable[T]:
        """Lo que sale hacia el venue termina aunque se cancele a quien lo espera."""
        task = asyncio.ensure_future(work)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return asyncio.shield(task)

    @property
    def in_flight(self) -> int:
        return len(self._inflight)

    async def drain(self) -> None:
        """Espera a los envíos y cancelaciones en vuelo (antes de salir)."""
        while self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    def can_trade(self, venue: VenueId) -> bool:
        return venue in self._venues

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    async def prepare(self, cmd: PlaceOrder) -> Pending | Rejected:
        client_id = self._new_client_id()
        if not isinstance(cmd.qty, Percent):
            req = OrderRequest(cmd.venue, cmd.side, cmd.qty, cmd.price, client_id)
            return await self._validate(req)
        sized = await self._size(cmd, cmd.qty, client_id)
        if isinstance(sized, Rejected):
            return sized
        req, basis = sized
        return await self._validate(req, basis=basis)

    async def _size(
        self, cmd: PlaceOrder, pct: Percent, client_id: str
    ) -> tuple[OrderRequest, str] | Rejected:
        """Cantidad a partir del % del saldo del venue (RF-17, D-21), con los mismos
        saldos con los que se valida. Sin saldo suficiente para un lote, rechazo."""
        req = OrderRequest(cmd.venue, cmd.side, ZERO, cmd.price, client_id)
        if bad := self._unavailable(req):
            return self._reject(req, bad)
        venue, spec = self._venues[cmd.venue], self._hub.state[cmd.venue].spec
        assert spec is not None  # _unavailable lo comprueba
        try:
            _, balances = await self._snapshot(cmd.venue, venue)
        except (VenueError, httpx.HTTPError) as exc:
            return self._reject(req, [self._unreadable(cmd.venue, exc)])
        s = size_from_percent(cmd.side, pct.value, cmd.price, balances, spec)
        basis = f"{s.pct} % de {s.basis:.8f} {asset_label(s.asset)} en {NAMES[cmd.venue]}"
        req = OrderRequest(cmd.venue, cmd.side, s.qty, cmd.price, client_id)
        if s.qty <= 0:
            msg = f"{basis}: no llega a un lote ({spec.lot_size:f} {asset_label(spec.base)})"
            return self._reject(req, [Violation("insufficient_balance", msg)])
        return req, basis

    @asynccontextmanager
    async def _sending(self, *venues: VenueId) -> AsyncIterator[None]:
        """Los locks de los venues, en orden fijo para que dos dobles no se bloqueen."""
        async with AsyncExitStack() as stack:
            for vid in sorted(set(venues)):
                await stack.enter_async_context(self._locks.setdefault(vid, asyncio.Lock()))
            yield

    async def confirm(self, pending: Pending) -> Sent | Failed | Uncertain | Rejected:
        async with self._sending(pending.req.venue):
            checked = await self._validate(pending.req)
            if isinstance(checked, Rejected):
                return checked
            return await self._send(pending.req, {})

    async def _send(self, req: OrderRequest, extra: dict[str, Any]) -> Sent | Failed | Uncertain:
        return await self._protected(self._send_now(req, extra))

    async def _send_now(
        self, req: OrderRequest, extra: dict[str, Any]
    ) -> Sent | Failed | Uncertain:
        known, sent_at = self._known(req.venue), self._clock()
        try:
            self._journal.write(
                "order_sent",
                venue=req.venue,
                client_id=req.client_id,
                data={"side": req.side, "qty": req.qty, "price": req.price, **extra},
            )
        except OSError as exc:
            return Failed(req, f"no se pudo escribir el journal ({exc}); no se envió", local=True)
        try:
            order = await self._venues[req.venue].place_limit(req)
        except UncertainError as exc:
            self._record(
                "order_unknown",
                venue=req.venue,
                client_id=req.client_id,
                data={"error": describe(exc), **extra},
            )
            self.uncertain[req.client_id] = UncertainOrder(req, sent_at, known, extra)
            return Uncertain(req, describe(exc))
        except (VenueError, httpx.HTTPError) as exc:
            # sin conectar la orden no salió: que no parezca un rechazo del venue (D-51)
            error = describe(exc)
            if isinstance(exc, NOT_SENT):
                error = f"no enviada, sin conexión ({error})"
            self._record(
                "order_error", venue=req.venue, client_id=req.client_id,
                data={"error": error, **extra},
            )  # fmt: skip
            return Failed(req, error)
        self._record(
            "order_ack",
            note=f"orden {order.order_id} enviada, pero sin order_ack en el journal",
            venue=req.venue,
            client_id=req.client_id,
            data={"order_id": order.order_id, "status": order.status, **extra},
        )
        self._acked.setdefault(req.venue, set()).add(order.order_id)
        if not order.is_done:
            self._just_sent.setdefault(req.venue, {})[order.order_id] = order
        return Sent(order)

    # -- orden doble ------------------------------------------------------------

    def subscribe_doubles(self, listener: Callable[[str], None]) -> None:
        self._double_listeners.append(listener)

    def _changed(self, double_id: str) -> None:
        for listener in self._double_listeners:
            listener(double_id)

    async def prepare_double(self, cmd: DoubleOrder) -> PendingDouble | DoubleRejected:
        double_id = self._new_double_id()
        buy = OrderRequest(cmd.buy_venue, "buy", cmd.qty, cmd.buy_price, self._new_client_id())
        sell = OrderRequest(cmd.sell_venue, "sell", cmd.qty, cmd.sell_price, self._new_client_id())
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
        """Con `lead` (el bot, D-75), solo sale la pierna de ese venue; la otra
        queda `held` hasta `send_held` o `skip_held`."""
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

    async def send_held(self, double_id: str, qty: Decimal) -> Sent | Failed | Uncertain:
        """Envía la pierna `held` de la doble por `qty` (D-75). Ya se validó con la
        primera al confirmar; `qty` no pasa de lo validado."""
        state = self.doubles[double_id]
        (side,) = (leg.req.side for leg in state.legs if leg.status == "held")
        state = state.resize(side, qty)
        self.doubles[double_id] = state
        req = state.leg(side).req
        async with self._sending(req.venue):
            result = await self._send(req, {"double": double_id})
        self._apply(double_id, result)
        self._changed(double_id)
        return result

    def skip_held(self, double_id: str) -> None:
        """La pierna `held` no se envía (D-75): cuenta como cerrada con 0 lleno."""
        state = self.doubles[double_id]
        for leg in state.legs:
            if leg.status == "held":
                state = state.skip(leg.req.side)
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
        """Órdenes de la cuenta (AccountService): actualizan las piernas, y una
        orden recién enviada deja de contarse aparte en cuanto la cuenta la ve."""
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
        """Ids que acaban de quedar con pierna colgada; escribe `hanging_leg` una vez."""
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
                        leg.req.side: {
                            "venue": leg.req.venue,
                            "client_id": leg.req.client_id,
                            "order_id": leg.order_id,
                            "status": leg.status,
                            "qty": leg.req.qty,
                            "filled": leg.filled,
                        }
                        for leg in state.legs
                    },
                },
            )
            new.append(double_id)
            self._changed(double_id)
        return new

    # -- envíos inciertos (RNF-12, D-26) ------------------------------------------

    def _known(self, vid: VenueId) -> frozenset[str]:
        """Órdenes del venue que ya se conocen: abiertas de la cuenta y recién enviadas."""
        st = self._account.state.get(vid) if self._account is not None else None
        ids = set(st.orders) if st is not None else set()
        return frozenset(ids | set(self._just_sent.get(vid, {})))

    async def reconcile(self) -> list[Reconciled]:
        """Una vuelta: cada venue con inciertas se consulta una vez. Si el venue no
        responde, se deja para la siguiente (sin mirar no se da nada por inexistente)."""
        by_venue: dict[VenueId, list[UncertainOrder]] = {}
        for u in self.uncertain.values():
            by_venue.setdefault(u.req.venue, []).append(u)
        out: list[Reconciled] = []
        for vid, pending in by_venue.items():
            venue = self._venues[vid]
            since = min(u.sent_at for u in pending) - WINDOW_MS
            try:
                orders = await venue.recent_orders(since)
            except (VenueError, httpx.HTTPError):
                continue
            now = self._clock()
            for u in pending:
                # tampoco las que la terminal envió después con ack: son otras
                known = u.known | self._acked.get(vid, set())
                by_id = venue.keeps_client_id
                found = candidates(u.req, orders, u.sent_at, known, by_client_id=by_id)
                if len(found) == 1:
                    out.append(self._found(u, found[0]))
                elif found and not u.warned:
                    u.warned = True
                    msg = (
                        f"orden incierta {u.req.client_id}: {len(found)} órdenes iguales en "
                        f"{NAMES[vid]} ({', '.join(o.order_id for o in found)}); sigue incierta, "
                        "revísala a mano"
                    )
                    out.append(Reconciled(u.req.client_id, vid, "ambiguous", msg))
                elif not found and now - u.sent_at > GIVE_UP_MS:
                    out.append(self._missing(u))
        return out

    def _found(self, u: UncertainOrder, order: Order) -> Reconciled:
        req = u.req
        del self.uncertain[req.client_id]
        order = replace(order, client_id=req.client_id)  # Neoxa no lo devuelve (PA-09)
        self._record(
            "order_ack",
            venue=req.venue,
            client_id=req.client_id,
            data={
                "order_id": order.order_id,
                "status": order.status,
                "reconciled": True,
                **u.extra,
            },
        )
        self._acked.setdefault(req.venue, set()).add(order.order_id)
        if not order.is_done:
            self._just_sent.setdefault(req.venue, {})[order.order_id] = order
        self._update_doubles(order)
        msg = (
            f"orden incierta {req.client_id}: existe en {NAMES[req.venue]} como "
            f"{order.order_id} ({order.status})"
        )
        return Reconciled(req.client_id, req.venue, "found", msg)

    def _missing(self, u: UncertainOrder) -> Reconciled:
        req = u.req
        del self.uncertain[req.client_id]
        error = f"no existe en {NAMES[req.venue]} tras {GIVE_UP_MS // 1000} s"
        self._record(
            "order_error",
            venue=req.venue,
            client_id=req.client_id,
            data={"error": error, "reconciled": True, **u.extra},
        )
        for double_id, state in list(self.doubles.items()):
            leg = state.leg(req.side)
            if leg.req.client_id == req.client_id and leg.status == "unknown":
                self.doubles[double_id] = state.fail(req.side, error)
                self._changed(double_id)
        msg = f"orden incierta {req.client_id}: {error}; no se creó"
        return Reconciled(req.client_id, req.venue, "missing", msg)

    # -- cancelar ---------------------------------------------------------------

    async def plan_cancel(
        self, cmd: CancelOrder | CancelVenue | CancelAll
    ) -> CancelPlan | CancelRefused:
        if isinstance(cmd, CancelVenue) and cmd.venue not in self._venues:
            return CancelRefused(f"{NAMES[cmd.venue]}: sin keys, solo lectura")
        venues = [cmd.venue] if isinstance(cmd, CancelVenue) else list(self._venues)
        try:
            found = {vid: tuple(await self._venues[vid].open_orders()) for vid in venues}
        except (VenueError, httpx.HTTPError) as exc:
            return CancelRefused(f"no se pudieron leer las órdenes abiertas ({describe(exc)})")
        if isinstance(cmd, CancelOrder):
            hits = {
                vid: tuple(o for o in orders if o.order_id == cmd.order_id)
                for vid, orders in found.items()
            }
            hits = {vid: o for vid, o in hits.items() if o}
            if not hits:
                return CancelRefused(f"no hay ninguna orden abierta {cmd.order_id}")
            if len(hits) > 1:
                return CancelRefused(f"{cmd.order_id} está en los dos venues: usa cancel <venue>")
            return CancelPlan("order", hits)
        found = {vid: orders for vid, orders in found.items() if orders}
        if not found:
            return CancelRefused("no hay órdenes abiertas que cancelar")
        return CancelPlan("venue" if isinstance(cmd, CancelVenue) else "all", found)

    async def cancel_double(self, double_id: str) -> CancelResult:
        """Cancela las piernas de una doble que siguen abiertas en su venue (el bot,
        al pasar `fill_timeout_ms`). Las inciertas no tienen id: no se tocan."""
        state = self.doubles[double_id]
        orders: dict[VenueId, tuple[Order, ...]] = {}
        for leg in state.legs:
            if leg.done or leg.order_id is None or leg.status == "unknown":
                continue
            r = leg.req
            order = Order(
                r.venue, leg.order_id, r.client_id, r.side, r.price, r.qty, leg.filled, "open",
                self._clock(),
            )  # fmt: skip
            orders[r.venue] = (order,)
        if not orders:
            return CancelResult({}, {})
        return await self.execute_cancel(CancelPlan("order", orders))

    async def execute_cancel(self, plan: CancelPlan) -> CancelResult:
        """Los venues a la vez; cada uno con su resultado aunque otro falle."""
        results = await asyncio.gather(
            *(
                self._protected(self._cancel_in(vid, plan.scope, orders))
                for vid, orders in plan.orders.items()
            )
        )
        remaining = {vid: n for vid, n, _ in results}
        errors = {vid: err for vid, _, err in results if err}
        return CancelResult(remaining, errors)

    async def _cancel_in(
        self, vid: VenueId, scope: str, orders: tuple[Order, ...]
    ) -> tuple[VenueId, int, str | None]:
        venue = self._venues[vid]
        ids = [o.order_id for o in orders]
        client_id = orders[0].client_id if scope == "order" else None
        self._record(
            "cancel_sent", venue=vid, client_id=client_id, data={"scope": scope, "order_ids": ids}
        )
        try:
            if scope == "order":
                await venue.cancel(ids[0])
            else:
                await venue.cancel_all()
            still = await venue.open_orders()
            # una orden: si sigue ella; venue o todas: cualquiera del par (CA-05)
            left = sum(o.order_id in ids for o in still) if scope == "order" else len(still)
        except (VenueError, httpx.HTTPError) as exc:
            self._record(
                "error",
                venue=vid,
                client_id=client_id,
                data={"action": "cancel", "scope": scope, "order_ids": ids, "error": describe(exc)},
            )
            return vid, len(orders), describe(exc)
        self._record(
            "cancel_ack", venue=vid, client_id=client_id, data={"scope": scope, "remaining": left}
        )
        return vid, left, None

    # -- validación -------------------------------------------------------------

    async def _validate(
        self, req: OrderRequest, extra: dict[str, Any] | None = None, *, basis: str | None = None
    ) -> Pending | Rejected:
        violations = await self._violations(req)
        if violations:
            return self._reject(req, violations, extra)
        spec = self._hub.state[req.venue].spec
        assert spec is not None  # _violations lo comprueba
        return Pending(req, spec, req.notional, req.notional * spec.taker_fee, basis)

    def _reject(
        self, req: OrderRequest, violations: list[Violation], extra: dict[str, Any] | None = None
    ) -> Rejected:
        self._record(
            "reject_local",
            venue=req.venue,
            client_id=req.client_id,
            data={
                "side": req.side,
                "qty": req.qty,
                "price": req.price,
                "violations": [{"code": v.code, "message": v.message} for v in violations],
                **(extra or {}),
            },
        )
        return Rejected(req, tuple(violations))

    def _unavailable(self, req: OrderRequest) -> list[Violation]:
        """Sin keys o sin mercado cargado no hay nada más que validar."""
        if req.venue not in self._venues:
            return [Violation("read_only", f"{NAMES[req.venue]}: sin keys, solo lectura")]
        if self._hub.state[req.venue].spec is None:
            return [Violation("stale_data", f"{NAMES[req.venue]}: mercado aún sin cargar")]
        return []

    @staticmethod
    def _unreadable(vid: VenueId, exc: Exception) -> Violation:
        # sin saldos u órdenes no se puede validar: ante la duda, no se envía (D-16)
        msg = f"{NAMES[vid]}: no se pudieron leer saldos u órdenes ({describe(exc)})"
        return Violation("venue_error", msg)

    async def _violations(self, req: OrderRequest) -> list[Violation]:
        if bad := self._unavailable(req):
            return bad
        st = self._hub.state[req.venue]
        assert st.spec is not None
        try:
            # todos los venues con keys: el tope por orden es un % del total (D-19)
            snaps = {vid: await self._snapshot(vid, v) for vid, v in self._venues.items()}
        except (VenueError, httpx.HTTPError) as exc:
            return [self._unreadable(req.venue, exc)]
        open_orders, balances = snaps[req.venue]
        total = inventory(
            {vid: bal for vid, (_, bal) in snaps.items()},
            {vid: self._hub.state[vid].book.mid for vid in snaps},
        )
        ctx = RiskContext(
            market=st.spec,
            best_bid=st.book.best_bid,
            best_ask=st.book.best_ask,
            book_ts=self.fresh_at(req.venue),
            now=self._clock(),
            open_orders=open_orders,
            balances=balances,
            inventory_value=total.value,
        )
        return check(req, ctx, self._limits) + self._skew(req.venue)

    def _skew(self, vid: VenueId) -> list[Violation]:
        if not self._live:
            return []
        skew, cap = self._hub.clock_skew_ms(vid), self._limits.max_clock_skew_s
        if skew is None:
            msg = f"{NAMES[vid]}: desfase de reloj sin medir; no se envía hasta saberlo"
            return [Violation("clock_skew", msg)]
        if abs(skew) > cap * 1000:
            msg = (
                f"{NAMES[vid]}: reloj desfasado {skew / 1000:+.0f} s (máx. {cap} s); "
                "sincroniza el reloj del sistema (NTP)"
            )
            return [Violation("clock_skew", msg)]
        return []

    async def _snapshot(
        self, vid: VenueId, venue: Venue
    ) -> tuple[tuple[Order, ...], dict[str, Balance]]:
        """Órdenes abiertas y saldos para RiskCheck: de la cuenta si están al día,
        más las inciertas de ese venue (pueden existir, D-27)."""
        st = self._account.state.get(vid) if self._account is not None else None
        if (
            st is not None
            and st.balances is not None
            and st.balances_at is not None
            and self._clock() - st.balances_at <= CACHE_MAX_AGE_MS
        ):
            orders = dict(st.orders)
            for oid, order in self._just_sent.get(vid, {}).items():
                orders.setdefault(oid, order)
            balances = dict(st.balances)
        else:
            orders = {o.order_id: o for o in await venue.open_orders()}
            balances = await venue.balances()
        seen = {o.client_id for o in orders.values()}
        for u in self.uncertain.values():
            r = u.req
            if r.venue == vid and r.client_id not in seen:
                orders[f"?{r.client_id}"] = Order(
                    vid, f"?{r.client_id}", r.client_id, r.side, r.price, r.qty, ZERO, "open",
                    u.sent_at,
                )  # fmt: skip
        return tuple(orders.values()), balances

    def fresh_at(self, venue: VenueId) -> int | None:
        """Último momento en que el book se sabe al día: el último mensaje del book
        o el último contraste con el REST que coincidió. La conexión viva ya no
        basta (D-76, corrige D-13); un book en duda no está al día."""
        st = self._hub.state[venue]
        if not st.book.valid or st.suspect:
            return None
        times = [t for t in (st.book.received_at, st.checked_at) if t is not None]
        return max(times) if times else None
