"""AccountService: private channel state, open orders, fills and balances.

For each venue with keys (design.md §6.5):
- Consumes `private_stream()`: `ConnState`s go to the status bar, open orders are
  kept (finished ones removed) and each `Fill` is written to the journal and asks
  for that venue's balances at once, like any change of an own order (it moves
  balance between available and locked).
- Asks `balances()` at startup and every `balances_every_s` (≤ 10 s, RF-05), counted
  from start to start of each read. On failure the previous ones stay and the error
  is noted.

A dropped stream reopens after `retry_s`, except `ReadOnlyError` (no keys), which
does not pass. Changes are notified as `(venue, what)` with `what` ∈ conn | orders |
fill | balances, and each private event as is to those asking with
`subscribe_events` (the legs of double orders, T-18). In dry-run venues are
`DryRunVenue` and everything is simulated.

At startup, in live, trades already on the venue (`PastFill`) are compared with the
journal of the last days (D-68): a fee corrected while the terminal was stopped goes
as `fill_fee`, and an unwritten fill of an order of ours as a recovered `fill`. The
rest is ignored.

Each loop (stream and balances of each venue) runs apart under `supervised` (RNF-14,
D-28): a bug is reported to `faults` and retried, without stopping the others.

Spec: RF-05, RF-08, RF-09, RF-11, RF-14, RNF-14 · design.md §6.5
Tasks: T-17, T-18, T-19, T-27, T-68
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
CATCHUP_DAYS = 7  # how much journal is read to catch up at startup (D-68)


@dataclass
class VenueAccount:
    id: VenueId
    conn: ConnState | None = None
    conn_at: int | None = None
    orders: dict[str, Order] = field(default_factory=dict)  # open only
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
        self._client_ids: dict[str, str] = {}  # order_id → client_id, finished ones too
        self._refresh = {venue_id: asyncio.Event() for venue_id in self._venues}
        self._logged: dict[tuple[VenueId, str], str] = {}  # last error noted
        self.state: dict[VenueId, VenueAccount] = {
            venue_id: VenueAccount(venue_id) for venue_id in self._venues
        }
        self._known: JournalIndex | None = None  # read at the first `PastFill` (D-68)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def subscribe_events(self, listener: EventListener) -> None:
        self._event_listeners.append(listener)

    def _notify(self, venue: VenueId, what: str) -> None:
        for listener in self._listeners:
            listener(venue, what)

    async def run(self) -> None:
        loops: list[tuple[str, VenueId, Callable[[], Awaitable[None]]]] = []
        for venue_id, v in self._venues.items():
            loops.append(
                ("private", venue_id, lambda venue_id=venue_id, v=v: self._stream(venue_id, v))
            )
            loops.append(
                ("balances", venue_id, lambda venue_id=venue_id, v=v: self._balances(venue_id, v))
            )
        await asyncio.gather(
            *(
                supervised(
                    work,
                    lambda error, where=where, venue_id=venue_id: self._crash(
                        venue_id, where, error
                    ),
                    retry_s=self._retry_s,
                )
                for where, venue_id, work in loops
            )
        )

    def _crash(self, venue_id: VenueId, where: str, error: Exception) -> None:
        self.state[venue_id].error = self.faults.report(where, error, venue=venue_id).error
        self._notify(venue_id, "conn")

    def apply(self, venue_id: VenueId, event: PrivateEvent) -> None:
        state = self.state[venue_id]
        for listener in self._event_listeners:
            listener(venue_id, event)
        if isinstance(event, ConnState):
            state.conn, state.conn_at = event, self._clock()
            self._notify(venue_id, "conn")
        elif isinstance(event, Order):
            self._logged.pop((venue_id, "private"), None)  # orders are arriving: the streak is over
            if event.client_id:
                self._client_ids[event.order_id] = event.client_id
            if event.is_done:
                state.orders.pop(event.order_id, None)
            else:
                state.orders[event.order_id] = event
            self._refresh[venue_id].set()  # opening or cancelling moves balance to/from locked
            self._notify(venue_id, "orders")
        elif isinstance(event, Fill):
            state.recent_fills.append(event)
            self._journal_fill(event)
            self._refresh[venue_id].set()
            self._notify(venue_id, "fill")
        elif isinstance(event, PastFill):
            self._catch_up(event.fill)
        elif isinstance(event, FeeUpdate):  # only the fee changes: neither balances nor legs
            for i, f in enumerate(state.recent_fills):
                if f.trade_id == event.trade_id:
                    state.recent_fills[i] = replace(f, fee=event.fee, fee_asset=event.fee_asset)
            self._journal_fee(event)
            self._notify(venue_id, "fill")

    def _journal_error(self, venue_id: VenueId, where: str, error: str) -> None:
        """One `error` per streak (RF-11): the same failure every 3 s does not flood the
        journal or the diagnostic log (RNF-19)."""
        if self._logged.get((venue_id, where)) == error:
            return
        self._logged[(venue_id, where)] = error
        log.warning("%s: %s: %s", venue_id, where, error)
        if self._journal is not None:
            self._journal.write("error", venue=venue_id, data={"where": where, "error": error})

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
        """The corrected fee goes apart: the fill's line is not touched (RF-11, D-64)."""
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

    # -- catching up at startup (D-68) ----------------------------------------

    def _catch_up(self, f: Fill) -> None:
        """A trade already there: write what the journal does not know. Live only: in
        dry-run real trades are not of the simulated session."""
        if self._journal is None or self._journal.mode != "live":
            return
        what, client_id = decide(f, self._index())
        if what == "fee":
            u = FeeUpdate(f.venue, f.trade_id, f.order_id, f.fee, f.fee_asset, f.ts)
            log.info("%s: fee of %s corrected while not watching", f.venue, f.trade_id)
            self._journal_fee(u, client_id)
            self._notify(f.venue, "fill")
        elif what == "fill":
            log.warning("%s: unwritten fill %s of an order of ours", f.venue, f.trade_id)
            self._journal_fill(f, client_id, {"recovered": True, "trade_ts": f.ts})
            self._refresh[f.venue].set()
            self._notify(f.venue, "fill")

    def _index(self) -> JournalIndex:
        if self._known is None:
            self._known = index(self._journal_rows())
        return self._known

    def _journal_rows(self) -> list[dict[str, object]]:
        """Rows of the journals of the last `CATCHUP_DAYS` days."""
        assert self._journal is not None
        first = datetime.fromtimestamp(self._clock() / 1000 - CATCHUP_DAYS * 86_400, UTC)
        lines: list[str] = []
        for path in sorted(self._journal.directory.glob("journal-*.jsonl")):
            if path.stem.removeprefix("journal-") >= first.strftime("%Y%m%d"):
                try:
                    lines += path.read_text(encoding="utf-8").splitlines()
                except OSError as error:  # unable to read, nothing is recovered: it does not break
                    log.warning("could not read %s to catch up: %s", path, error)
        return read_rows(lines)[0]

    async def _stream(self, venue_id: VenueId, venue: Venue) -> None:
        state = self.state[venue_id]
        while True:
            try:
                async with aclosing(venue.private_stream()) as stream:
                    async for event in stream:
                        self.apply(venue_id, event)
            except ReadOnlyError as error:
                state.error = describe(error)
                self._notify(venue_id, "conn")
                return
            except (VenueError, httpx.HTTPError) as error:
                state.error = describe(error)
                self._journal_error(venue_id, "private", state.error)
                self._notify(venue_id, "conn")
            await asyncio.sleep(self._retry_s)

    async def _balances(self, venue_id: VenueId, venue: Venue) -> None:
        state = self.state[venue_id]
        refresh = self._refresh[venue_id]
        loop = asyncio.get_running_loop()
        while True:
            refresh.clear()
            started = loop.time()
            try:
                state.balances = await venue.balances()
                state.balances_at, state.error = self._clock(), None
                self._logged.pop((venue_id, "balances"), None)
            except ReadOnlyError as error:
                state.error = describe(error)
                return
            except (VenueError, httpx.HTTPError) as error:
                state.error = describe(error)
                self._journal_error(venue_id, "balances", state.error)
            self._notify(venue_id, "balances")
            # each read starts `balances_every_s` after the previous one started, not after
            # it ended: otherwise the real interval passes 10 s (CA-01)
            wait = max(0.0, started + self._balances_every_s - loop.time())
            try:
                await asyncio.wait_for(refresh.wait(), wait)
            except TimeoutError:
                pass
