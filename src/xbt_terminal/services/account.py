"""AccountService: estado del canal privado, órdenes abiertas, fills y balances.

Por cada venue con keys (design.md §6.5):
- Consume `private_stream()`: los `ConnState` van a la barra de estado, las
  órdenes abiertas se guardan (las terminadas se quitan) y cada `Fill` se
  escribe en el journal y pide los balances de ese venue en el acto, igual que
  cualquier cambio de una orden propia (mueve saldo entre disponible y bloqueado).
- Pide `balances()` al arrancar y cada `balances_every_s` (≤ 10 s, RF-05),
  contado de inicio a inicio de cada lectura. Si
  falla, se conservan los anteriores y se anota el error.

Un stream que se corta se vuelve a abrir tras `retry_s`, salvo `ReadOnlyError`
(sin keys), que no es pasajero. Los cambios se avisan como `(venue, qué)` con
`qué` ∈ conn | orders | fill | balances, y cada evento privado tal cual a quien
lo pida con `subscribe_events` (las piernas de las órdenes dobles, T-18). En
dry-run los venues son `DryRunVenue` y todo es simulado.

Al arrancar, en live, los trades que el venue ya tenía (`PastFill`) se comparan
con el journal de los últimos días (D-68): una fee corregida mientras la
terminal estaba parada va como `fill_fee`, y un fill de una orden nuestra que
no se apuntó, como `fill` recuperado. Lo demás se ignora.

Cada bucle (stream y saldos de cada venue) corre aparte bajo `supervised`
(RNF-14, D-28): un bug se reporta a `faults` y se reintenta, sin parar a los demás.

Spec: RF-05, RF-08, RF-09, RF-11, RF-14, RNF-14 · design.md §6.5
Tareas: T-17, T-18, T-19, T-27, T-68
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import httpx

from ..adapters.base import PrivateEvent, ReadOnlyError, Venue, VenueError, describe
from ..adapters.streaming import now_ms
from ..core.catchup import JournalIndex, decide, index
from ..core.journal import Journal
from ..core.models import Balance, ConnState, FeeUpdate, Fill, Order, PastFill, VenueId
from ..core.results import read_rows
from .faults import Faults, supervised

log = logging.getLogger(__name__)

Listener = Callable[[VenueId, str], None]
EventListener = Callable[[VenueId, PrivateEvent], None]
FILLS_KEPT = 100
CATCHUP_DAYS = 7  # cuánto journal se mira para ponerse al día al arrancar (D-68)


@dataclass
class VenueAccount:
    id: VenueId
    conn: ConnState | None = None
    conn_at: int | None = None
    orders: dict[str, Order] = field(default_factory=dict)  # solo abiertas
    balances: dict[str, Balance] | None = None
    balances_at: int | None = None
    recent_fills: deque[Fill] = field(default_factory=lambda: deque(maxlen=FILLS_KEPT))
    error: str | None = None

    @property
    def fills(self) -> list[Fill]:
        return list(self.recent_fills)


class AccountService:
    def __init__(
        self,
        venues: Mapping[VenueId, Venue],
        *,
        journal: Journal | None = None,
        balances_every_s: float = 10.0,
        retry_s: float = 5.0,
        clock: Callable[[], int] = now_ms,
        faults: Faults | None = None,
    ) -> None:
        self._venues = dict(venues)
        self._journal = journal
        self.faults = faults or Faults(journal, clock=clock)
        self._balances_every_s = balances_every_s
        self._retry_s = retry_s
        self._clock = clock
        self._listeners: list[Listener] = []
        self._event_listeners: list[EventListener] = []
        self._client_ids: dict[str, str] = {}  # order_id → client_id, también de las terminadas
        self._refresh = {vid: asyncio.Event() for vid in self._venues}
        self._logged: dict[tuple[VenueId, str], str] = {}  # último error anotado
        self.state: dict[VenueId, VenueAccount] = {vid: VenueAccount(vid) for vid in self._venues}
        self._known: JournalIndex | None = None  # se lee al primer `PastFill` (D-68)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def subscribe_events(self, listener: EventListener) -> None:
        self._event_listeners.append(listener)

    def _notify(self, venue: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(venue, what)

    async def run(self) -> None:
        loops: list[tuple[str, VenueId, Callable[[], Awaitable[None]]]] = []
        for vid, v in self._venues.items():
            loops.append(("private", vid, lambda vid=vid, v=v: self._stream(vid, v)))
            loops.append(("balances", vid, lambda vid=vid, v=v: self._balances(vid, v)))
        await asyncio.gather(
            *(
                supervised(
                    work,
                    lambda exc, where=where, vid=vid: self._crash(vid, where, exc),
                    retry_s=self._retry_s,
                )
                for where, vid, work in loops
            )
        )

    def _crash(self, vid: VenueId, where: str, exc: Exception) -> None:
        self.state[vid].error = self.faults.report(where, exc, venue=vid).error
        self._notify(vid, "conn")

    def apply(self, vid: VenueId, event: PrivateEvent) -> None:
        st = self.state[vid]
        for listener in self._event_listeners:
            listener(vid, event)
        if isinstance(event, ConnState):
            st.conn, st.conn_at = event, self._clock()
            self._notify(vid, "conn")
        elif isinstance(event, Order):
            self._logged.pop((vid, "private"), None)  # llegan órdenes: la racha acabó
            if event.client_id:
                self._client_ids[event.order_id] = event.client_id
            if event.is_done:
                st.orders.pop(event.order_id, None)
            else:
                st.orders[event.order_id] = event
            self._refresh[vid].set()  # abrir o cancelar mueve saldo a/desde bloqueado
            self._notify(vid, "orders")
        elif isinstance(event, Fill):
            st.recent_fills.append(event)
            self._journal_fill(event)
            self._refresh[vid].set()
            self._notify(vid, "fill")
        elif isinstance(event, PastFill):
            self._catch_up(event.fill)
        elif isinstance(event, FeeUpdate):  # solo cambia la fee: ni saldos ni piernas
            for i, f in enumerate(st.recent_fills):
                if f.trade_id == event.trade_id:
                    st.recent_fills[i] = replace(f, fee=event.fee, fee_asset=event.fee_asset)
            self._journal_fee(event)
            self._notify(vid, "fill")

    def _journal_error(self, vid: VenueId, where: str, error: str) -> None:
        """Un `error` por racha (RF-11): el mismo fallo cada 3 s no inunda el journal
        ni el log de diagnóstico (RNF-19)."""
        if self._logged.get((vid, where)) == error:
            return
        self._logged[(vid, where)] = error
        log.warning("%s: %s: %s", vid, where, error)
        if self._journal is not None:
            self._journal.write("error", venue=vid, data={"where": where, "error": error})

    def _journal_fill(
        self, f: Fill, client_id: str | None = None, extra: dict[str, object] | None = None
    ) -> None:
        if self._journal is None:
            return
        if self._known is not None:
            self._known.fee(f.venue, f.trade_id, f.fee, f.fee_asset)
        self._journal.write(
            "fill",
            venue=f.venue,
            client_id=client_id or (self._client_ids.get(f.order_id) if f.order_id else None),
            data={
                "trade_id": f.trade_id,
                "order_id": f.order_id,
                "side": f.side,
                "qty": f.qty,
                "price": f.price,
                "fee": f.fee,
                "fee_asset": f.fee_asset,
                **(extra or {}),
            },
        )

    def _journal_fee(self, u: FeeUpdate, client_id: str | None = None) -> None:
        """La fee corregida va aparte: la línea del fill no se toca (RF-11, D-64)."""
        if self._journal is None:
            return
        if self._known is not None:
            self._known.fee(u.venue, u.trade_id, u.fee, u.fee_asset)
        self._journal.write(
            "fill_fee",
            venue=u.venue,
            client_id=client_id or (self._client_ids.get(u.order_id) if u.order_id else None),
            data={
                "trade_id": u.trade_id,
                "order_id": u.order_id,
                "fee": u.fee,
                "fee_asset": u.fee_asset,
            },
        )

    # -- ponerse al día al arrancar (D-68) ----------------------------------------

    def _catch_up(self, f: Fill) -> None:
        """Un trade que ya estaba: se apunta lo que el journal no sabe. Solo en
        live: en dry-run los trades reales no son de la sesión simulada."""
        if self._journal is None or self._journal.mode != "live":
            return
        what, client_id = decide(f, self._index())
        if what == "fee":
            u = FeeUpdate(f.venue, f.trade_id, f.order_id, f.fee, f.fee_asset, f.ts)
            log.info("%s: fee de %s corregida mientras no se miraba", f.venue, f.trade_id)
            self._journal_fee(u, client_id)
            self._notify(f.venue, "fill")
        elif what == "fill":
            log.warning("%s: fill %s de una orden nuestra sin apuntar", f.venue, f.trade_id)
            self._journal_fill(f, client_id, {"recovered": True, "trade_ts": f.ts})
            self._refresh[f.venue].set()
            self._notify(f.venue, "fill")

    def _index(self) -> JournalIndex:
        if self._known is None:
            self._known = index(self._journal_rows())
        return self._known

    def _journal_rows(self) -> list[dict[str, object]]:
        """Las filas de los journals de los últimos `CATCHUP_DAYS` días."""
        assert self._journal is not None
        first = datetime.fromtimestamp(self._clock() / 1000 - CATCHUP_DAYS * 86_400, UTC)
        lines: list[str] = []
        for path in sorted(self._journal.directory.glob("journal-*.jsonl")):
            if path.stem.removeprefix("journal-") >= first.strftime("%Y%m%d"):
                try:
                    lines += path.read_text(encoding="utf-8").splitlines()
                except OSError as exc:  # sin poder leer, no se recupera nada: no rompe
                    log.warning("no se pudo leer %s para ponerse al día: %s", path, exc)
        return read_rows(lines)[0]

    async def _stream(self, vid: VenueId, venue: Venue) -> None:
        st = self.state[vid]
        while True:
            try:
                async with aclosing(venue.private_stream()) as stream:
                    async for event in stream:
                        self.apply(vid, event)
            except ReadOnlyError as exc:
                st.error = describe(exc)
                self._notify(vid, "conn")
                return
            except (VenueError, httpx.HTTPError) as exc:
                st.error = describe(exc)
                self._journal_error(vid, "private", st.error)
                self._notify(vid, "conn")
            await asyncio.sleep(self._retry_s)

    async def _balances(self, vid: VenueId, venue: Venue) -> None:
        st = self.state[vid]
        refresh = self._refresh[vid]
        loop = asyncio.get_running_loop()
        while True:
            refresh.clear()
            started = loop.time()
            try:
                st.balances = await venue.balances()
                st.balances_at, st.error = self._clock(), None
                self._logged.pop((vid, "balances"), None)
            except ReadOnlyError as exc:
                st.error = describe(exc)
                return
            except (VenueError, httpx.HTTPError) as exc:
                st.error = describe(exc)
                self._journal_error(vid, "balances", st.error)
            self._notify(vid, "balances")
            # cada lectura empieza `balances_every_s` después de la anterior, no
            # después de que termine: si no, el intervalo real pasa de 10 s (CA-01)
            wait = max(0.0, started + self._balances_every_s - loop.time())
            try:
                await asyncio.wait_for(refresh.wait(), wait)
            except TimeoutError:
                pass
