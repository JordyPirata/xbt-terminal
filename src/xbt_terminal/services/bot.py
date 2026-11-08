"""BotService: the taker arbitrage bot's loop over OrderService (phase 2).

It wakes on each book or balance change (and every `tick_s`, for the cooldown), and
if `armed` builds each venue's view (`VenueView`) and calls `core/bot.decide`. A
venue is blocked if (RF-19):

- it has no keys or its market has not loaded;
- its book is invalid or older than `risk.stale_after_s` (local clock);
- its market connection is not `up`, or the hub or account have an error;
- in live, its clock skew is unmeasured or above `max_clock_skew_s`;
- the account has no balances.

With `max_move_pct` (D-71) it does not fire either if a venue's mid moved more than
that % in the last `move_window_ms`: the bot keeps the mid of each book change and
`bot_fire` notes each venue's move, filter or not.

And it does not fire while there is an uncertain order or an unfinished double (the
bot's or manual): one double at a time.

A shot (`Fire`) is a normal double order, with no dialog (arming was the
confirmation, RF-18): `prepare_double` + `confirm_double`, so it passes RiskCheck,
venue locks, uncertain handling and journal like any other. Then:

1. Wait for both legs to end, up to `fill_timeout_ms`. If one is rejected or left
   uncertain, no wait.
2. Cancel what stays open (`cancel_double`).
3. Wait up to `settle_s` for the legs to close (Neoxa reports by polling).
4. Equal fills → `cooldown`. Otherwise, or with a rejection, an uncertain leg or one
   that did not close → `halted` with the reason (RF-21). Nothing is hedged or retried.

With `lead_venue` (D-75) only that venue's leg goes first (Neoxa matches on receipt,
so the ack already says if it filled). What did not fill is cancelled at once and,
when the first closes, the other goes for what filled, rounded down to the lot, and
follows the steps above. If nothing filled, the other does not go: `bot_miss` and
`cooldown`, not counted as a failure. If the cancel fails because it had filled, the
account brings it when the order closes (Neoxa, by polling).

Without daily cap for another lot (`Skip("day_cap")`) the bot goes `halted`, as when
a shot uses it up (RF-22).

A RiskCheck rejection before sending counts as a failure and returns to `armed`
(`core/bot.rejected`). A bug in the loop goes `halted` and is reported to `faults`;
a double in flight gets a cancel attempt.

Journal (RF-23): `bot_state` on each state change and `bot_fire` on each shot, with
`at` (ms) and `notional` to rebuild the counters at startup (`journal-<today>.jsonl`,
same mode).

Deadlines (`fill_timeout_ms`, `settle_s`) use the asyncio loop clock; bot states use
`clock` (epoch ms), like everything else.

Spec: RF-18 to RF-23, CA-08, CA-09 · design.md §6.9 · D-71, D-75 · Tasks: T-36, T-73,
T-77, T-92
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC
from decimal import Decimal

from ..adapters.streaming import now_ms
from ..config import BotConfig
from ..core.bot import (
    DAY_MS,
    BotLimits,
    BotState,
    FeeToken,
    Fire,
    Skip,
    VenueView,
    arm,
    capped,
    decide,
    disarm,
    finished,
    fired,
    price_move_pct,
    rejected,
    tick,
    token_fee,
)
from ..core.commands import DoubleOrder
from ..core.double import NAMES, DoubleState, Leg
from ..core.inventory import InventoryTarget
from ..core.journal import Journal
from ..core.models import BASE_LABEL, ZERO, VenueId
from .account import AccountService, VenueAccount
from .faults import Faults, supervised
from .market import MarketHub
from .orders import DoubleRejected, OrderService

log = logging.getLogger(__name__)

BOOK_LEVELS = 50  # plenty for the depth up to the limit price
SETTLE_S = 15.0  # after cancelling, until the legs close (Neoxa: 3 s polling)
TICK_S = 1.0
POLL_S = 0.05

Listener = Callable[[BotState], None]
STATUS_LABEL = {
    "off": "apagado",
    "armed": "armado",
    "firing": "disparando",
    "cooldown": "enfriando",
    "halted": "PARADO",
}


class BotService:
    def __init__(
        self,
        hub: MarketHub,
        orders: OrderService,
        account: AccountService | None,
        journal: Journal,
        config: BotConfig,
        *,
        live: bool,
        faults: Faults,
        clock: Callable[[], int] = now_ms,
        settle_s: float = SETTLE_S,
        tick_s: float = TICK_S,
        target: InventoryTarget | None = None,
        fee_tokens: Mapping[VenueId, FeeToken] | None = None,
    ) -> None:
        self._hub = hub
        self._orders = orders
        self._account = account
        self._journal = journal
        self._config = config
        self._limits = config.limits
        self._target = target  # inventory ratio (RF-26)
        self._fee_tokens = dict(fee_tokens or {})  # fee paid in token (RF-24, D-63)
        self._live = live
        self.faults = faults
        self._clock = clock
        self._settle_s = settle_s
        self._tick_s = tick_s
        self._wake = asyncio.Event()
        self._listeners: list[Listener] = []
        self._firing: str | None = None  # id of the double in flight
        # (time, mid) of each book change within `move_window_ms` (D-71)
        self._mids: dict[VenueId, deque[tuple[int, Decimal]]] = {}
        self.last_skip: Skip | None = None
        self.state = self._restore()
        hub.subscribe(self._on_change)
        if account is not None:
            account.subscribe(self._on_change)

    # -- control ----------------------------------------------------------------

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def refusal(self) -> str | None:
        """Why it cannot be armed, or None (RF-18)."""
        if self._live and not self._config.enabled:
            return "en live el bot solo se arma con bot.enabled = true en config.toml"
        return None

    @property
    def summary(self) -> str:
        """Parameters, for the arming confirmation."""
        lim = self._limits
        return (
            "BOT DE ARBITRAJE TAKER\n"
            f"dispara cuando la neta mínima {lim.min_net_pct} % se cumple con slippage máx. "
            f"{lim.max_slippage_pct} % y fee taker en las dos piernas\n"
            f"{_walk_text(lim)}"
            f"{_move_text(lim)}"
            f"{_lead_text(lim)}"
            f"{self._token_text()}"
            f"hasta {lim.max_qty} {BASE_LABEL} por disparo · "
            f"{lim.max_daily_btc} BTC de compra al día · "
            f"{lim.max_trades_per_hour} disparos por hora · {lim.cooldown_s} s entre disparos\n"
            f"lo que no llene en {lim.fill_timeout_ms} ms se cancela; si una pierna queda "
            "colgada, incierta o rechazada, el bot se PARA y no cubre nada"
        )

    def status_text(self) -> str:
        st, now = self.state, self._clock()
        spent = st.day_btc if st.day == now // DAY_MS else ZERO
        parts = [f"bot {STATUS_LABEL[st.status]}"]
        if st.reason:
            parts.append(st.reason)
        if self.last_skip is not None and st.status == "armed":
            skip = self.last_skip
            parts.append(
                f"sin disparar: {skip.reason}" + (f" ({skip.detail})" if skip.detail else "")
            )
        parts += [self._token_status(vid, t) for vid, t in self._fee_tokens.items()]
        hits = sum(1 for t in st.fired_at if t // DAY_MS == now // DAY_MS)
        parts.append(f"disparos hoy {hits}, {spent:f} BTC de compra")
        return " · ".join(parts)

    def arm(self) -> str | None:
        """Arm the bot; return why it cannot be, or None (RF-18)."""
        if refusal := self.refusal():
            return refusal
        if self.state.status in ("armed", "firing", "cooldown"):
            return None
        self._set(arm(self.state))
        self._wake.set()
        return None

    def disarm(self) -> None:
        """Stop the bot. A double in flight runs its course until it closes."""
        if self.state.status != "off":
            self._set(disarm(self.state))

    async def run(self) -> None:
        await supervised(self._loop, self._crash, retry_s=self._tick_s)

    # -- loop --------------------------------------------------------------------

    def _on_change(self, venue: VenueId, what: str) -> None:
        if what == "book":
            self._sample_mid(venue)
        if what in ("book", "balances", "conn", "market"):
            self._wake.set()

    # -- mid move (RF-19, D-71) -----------------------------------------

    def _sample_mid(self, vid: VenueId) -> None:
        st = self._hub.state.get(vid)
        mid = st.book.mid if st is not None else None
        if mid is None:
            return
        now = self._clock()
        window = self._mids.setdefault(vid, deque())
        window.append((now, mid))
        self._forget(window, now)

    def _forget(self, window: deque[tuple[int, Decimal]], now: int) -> None:
        """Forget mids from before the window except the last: it ruled when the window
        began, and without it a jump after a quiet book gives 0 (D-72)."""
        while len(window) > 1 and now - window[1][0] > self._limits.move_window_ms:
            window.popleft()

    def _move_pct(self, vid: VenueId, now: int) -> Decimal | None:
        """Mid move in the window, with the current one: a quiet book gives 0."""
        window = self._mids.setdefault(vid, deque())
        self._forget(window, now)
        mids = [mid for _, mid in window]
        if (current := self._hub.state[vid].book.mid) is not None:
            mids.append(current)
        return price_move_pct(mids)

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), self._tick_s)
            except TimeoutError:
                pass
            self._wake.clear()
            self._set(tick(self.state, self._limits, self._clock()))
            if self.state.status == "armed":
                await self._evaluate()

    def _crash(self, exc: Exception) -> None:
        self.faults.report("bot", exc)
        reason = f"bug: {type(exc).__name__}: {exc}"
        self._set(replace(self.state, status="halted", reason=reason))
        if self._firing is not None:  # whatever stayed open of the double (RF-21)
            task = asyncio.ensure_future(self._orders.cancel_double(self._firing))
            task.add_done_callback(lambda t: t.exception())
            self._orders.skip_held(self._firing)  # the other leg no longer goes (D-75)
            self._firing = None

    async def _evaluate(self) -> None:
        now = self._clock()
        if self._orders.uncertain:
            self.last_skip = Skip("uncertain_open", "hay órdenes inciertas sin reconciliar")
            return
        if any(not d.done for d in self._orders.doubles.values()):
            self.last_skip = Skip("double_open", "hay una orden doble sin terminar")
            return
        views = self._views(now)
        result = decide(views, self.state, self._limits, now, self._target)
        if isinstance(result, Skip):
            was = self.last_skip.reason if self.last_skip is not None else None
            if result.reason == "volatile" and was != "volatile":
                log.info("bot: not firing, sharp move (%s)", result.detail)
            self.last_skip = result
            if result.reason == "day_cap":  # no cap for another lot: stop (RF-22)
                self._set(capped(self.state, self._limits))
            return
        self.last_skip = None
        await self._fire(result, {vid: v.move_pct for vid, v in views.items()})

    def _views(self, now: int) -> dict[VenueId, VenueView]:
        views: dict[VenueId, VenueView] = {}
        for vid, st in self._hub.state.items():
            if st.spec is None:
                continue
            bids, asks = st.book.best_levels(BOOK_LEVELS)
            # the account state is read once per venue (D-70)
            acct = self._account.state.get(vid) if self._account is not None else None
            balances = (acct.balances if acct is not None else None) or {}
            quote, base = balances.get(st.spec.quote), balances.get(st.spec.base)
            views[vid] = VenueView(
                spec=st.spec,
                bids=bids,
                asks=asks,
                quote_available=quote.available if quote is not None else ZERO,
                base_available=base.available if base is not None else ZERO,
                blocked=self._blocked(vid, now, acct),
                quote_total=quote.total if quote is not None else ZERO,
                base_total=base.total if base is not None else ZERO,
                mid=st.book.mid,
                net_fee=self._net_fee(vid, st.spec.taker_fee, acct),
                move_pct=self._move_pct(vid, now),
            )
        return views

    # -- fee paid in token (RF-24, D-63) -----------------------------------------

    def _acct(self, vid: VenueId) -> VenueAccount | None:
        return self._account.state.get(vid) if self._account is not None else None

    def _token_available(
        self, vid: VenueId, asset: str, acct: VenueAccount | None = None
    ) -> Decimal | None:
        acct = acct if acct is not None else self._acct(vid)
        if acct is None or acct.balances is None:
            return None
        bal = acct.balances.get(asset)
        return bal.available if bal is not None else ZERO

    def _net_fee(
        self, vid: VenueId, taker: Decimal, acct: VenueAccount | None = None
    ) -> Decimal | None:
        token = self._fee_tokens.get(vid)
        if token is None:
            return None
        return token_fee(taker, token, self._token_available(vid, token.asset, acct))

    def _token_text(self) -> str:
        """Confirmation lines with each venue's token fee, or nothing."""
        return "".join(
            f"{NAMES[vid]} fee taker -{t.discount_pct} % mientras haya {t.min_available} "
            f"{t.asset} disponibles; si no, la completa\n"
            for vid, t in self._fee_tokens.items()
        )

    def _token_status(self, vid: VenueId, token: FeeToken) -> str:
        available = self._token_available(vid, token.asset)
        if available is None:
            return f"{NAMES[vid]} fee completa: sin saldo de {token.asset}"
        if available < token.min_available:
            return f"{NAMES[vid]} fee completa: {available:f} {token.asset} < {token.min_available}"
        return f"{NAMES[vid]} fee con {token.asset} (-{token.discount_pct} %)"

    def _blocked(self, vid: VenueId, now: int, acct: VenueAccount | None) -> str | None:
        st = self._hub.state[vid]
        if not self._orders.can_trade(vid):
            return "sin keys"
        if not st.book.valid:
            return "book sin datos"
        fresh = self._orders.fresh_at(vid)
        stale_ms = self._orders.limits.stale_after_s * 1000
        if fresh is None or now - fresh > stale_ms:
            return "book viejo"
        if st.conn is None or st.conn.status != "up":
            return f"mercado {st.conn.status if st.conn else 'sin conexión'}"
        if st.error is not None:  # an error without text blocks too (D-51)
            return f"error del mercado: {st.error or '?'}"
        if acct is not None and acct.error is not None:
            return f"error de la cuenta: {acct.error or '?'}"
        if acct is None or acct.balances is None:
            return "sin saldos"
        if self._live:
            skew, cap = self._hub.clock_skew_ms(vid), self._orders.limits.max_clock_skew_s
            if skew is None or abs(skew) > cap * 1000:
                return "reloj desfasado o sin medir"
        return None

    # -- firing ------------------------------------------------------------------

    async def _fire(self, fire: Fire, move: Mapping[VenueId, Decimal | None]) -> None:
        now = self._clock()
        self._set(fired(self.state, fire, now))
        cmd = DoubleOrder(
            fire.qty, fire.buy_venue, fire.buy_price, fire.sell_venue, fire.sell_price
        )
        pending = await self._orders.prepare_double(cmd)
        double_id = pending.double_id if isinstance(pending, DoubleRejected) else pending.id
        self._record_fire(fire, double_id, now, move)
        if isinstance(pending, DoubleRejected):
            self._end(rejected(self.state, self._limits, ", ".join(pending.codes)))
            return
        self._firing = double_id
        lead = self._limits.lead_venue
        sent = await self._orders.confirm_double(pending, lead=lead)
        if isinstance(sent, DoubleRejected):
            self._firing = None
            self._end(rejected(self.state, self._limits, ", ".join(sent.codes)))
            return
        error = await (self._follow(double_id) if lead is None else self._lead(double_id, lead))
        self._firing = None
        self._end(finished(self.state, self._limits, self._clock(), error=error))

    def _end(self, state: BotState) -> None:
        """State when a shot ends. If it was turned off while firing, it stays `off` (D-40):
        without this it went to `cooldown` and re-armed by itself. A shot that ended badly
        goes `halted` anyway, so the warning is not lost."""
        if self.state.status == "off" and state.status != "halted":
            state = replace(state, status="off", reason=None)
        self._set(state)

    async def _lead(self, double_id: str, lead: VenueId) -> str | None:
        """First leg (D-75): what did not fill is cancelled now; the other goes for what
        filled or does not go. Returns what failed, like `_follow`."""
        loop = asyncio.get_running_loop()
        first = self._first(double_id, lead)
        cancel_errors: dict[VenueId, str] = {}
        if first.status not in ("filled", "rejected", "unknown"):
            cancel_errors = (await self._orders.cancel_double(double_id)).errors
            settle = loop.time() + self._settle_s
            while not first.done and first.status != "unknown" and loop.time() < settle:
                await asyncio.sleep(POLL_S)
                first = self._first(double_id, lead)
        if first.status != "filled" and (not first.done or first.status == "rejected"):
            self._orders.skip_held(double_id)  # uncertain, rejected or unclosed
            return _verdict(self._orders.doubles[double_id], cancel_errors)
        legs = self._orders.doubles[double_id].legs
        held = next(leg for leg in legs if leg.req.venue != lead)
        if first.filled == 0:
            self._orders.skip_held(double_id)
            self._record(
                "bot_miss",
                {"double": double_id, "venue": lead, "price": first.req.price,
                 "qty": first.req.qty},
            )  # fmt: skip
            log.info("bot: %s unfilled in double %s; the other leg stays", first.label, double_id)
            return None
        spec = self._hub.state[held.req.venue].spec
        assert spec is not None  # it fired with its book
        qty = spec.quantize_qty(first.filled)
        if qty < spec.min_qty or qty * held.req.price < spec.min_notional:
            self._orders.skip_held(double_id)  # below the minimum: it hangs
            return _verdict(self._orders.doubles[double_id], cancel_errors)
        await self._orders.send_held(double_id, qty)
        return await self._follow(double_id)

    def _first(self, double_id: str, lead: VenueId) -> Leg:
        return next(leg for leg in self._orders.doubles[double_id].legs if leg.req.venue == lead)

    async def _follow(self, double_id: str) -> str | None:
        """Wait, cancel the rest and say what failed (None if balanced)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._limits.fill_timeout_ms / 1000
        while loop.time() < deadline:
            d = self._orders.doubles[double_id]
            if d.done or any(leg.status in ("rejected", "unknown") for leg in d.legs):
                break
            await asyncio.sleep(POLL_S)
        cancel = await self._orders.cancel_double(double_id)
        settle = loop.time() + self._settle_s
        while loop.time() < settle:
            d = self._orders.doubles[double_id]
            if d.done or any(leg.status == "unknown" for leg in d.legs):
                break
            await asyncio.sleep(POLL_S)
        return _verdict(self._orders.doubles[double_id], cancel.errors)

    # -- state and journal ---------------------------------------------------------

    def _set(self, state: BotState) -> None:
        old, self.state = self.state, state
        if (old.status, old.reason) == (state.status, state.reason):
            return
        level = logging.WARNING if state.status == "halted" else logging.INFO
        log.log(level, "bot: %s%s", state.status, f" ({state.reason})" if state.reason else "")
        self._record("bot_state", {"status": state.status, "reason": state.reason})
        for listener in self._listeners:
            listener(state)

    def _record_fire(
        self, fire: Fire, double_id: str, now: int, move: Mapping[VenueId, Decimal | None]
    ) -> None:
        self._record(
            "bot_fire",
            {
                "double": double_id,
                "buy_venue": fire.buy_venue,
                "sell_venue": fire.sell_venue,
                "buy_price": fire.buy_price,
                "sell_price": fire.sell_price,
                "qty": fire.qty,
                "notional": fire.notional,
                "net": fire.net,
                "net_pct": fire.net_pct,
                "at": now,
                # to tune `max_move_pct` with data, filter or not (D-71)
                "move": dict(move),
                **({"lead": lead} if (lead := self._limits.lead_venue) else {}),
            },
        )

    def _record(self, event: str, data: dict[str, object]) -> None:
        try:
            self._journal.write(event, data=data)
        except OSError as exc:
            self.faults.report("journal", exc, note=f"{event} sin escribir en el journal")

    def _restore(self) -> BotState:
        """Today's counters from the journal (`bot_fire` of the same mode): the bot starts
        `off`, but the hourly and daily caps keep counting."""
        now = self._clock()
        # today's file according to the journal's clock, which names it
        day = self._journal.clock().astimezone(UTC).strftime("%Y%m%d")
        path = self._journal.directory / f"journal-{day}.jsonl"
        fires: list[tuple[int, Decimal]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
                if row["event"] != "bot_fire" or row["mode"] != self._journal.mode:
                    continue
                fires.append((int(row["data"]["at"]), Decimal(row["data"]["notional"])))
            except (ValueError, KeyError, TypeError):
                continue  # an odd line does not stop startup
        today = [(at, n) for at, n in fires if at // DAY_MS == now // DAY_MS]
        if not today:
            return BotState()
        restored = BotState(
            fired_at=tuple(at for at, _ in today),
            day=now // DAY_MS,
            day_btc=sum((n for _, n in today), ZERO),
            last_fire_at=max(at for at, _ in today),
        )
        return tick(restored, self._limits, now)


def _walk_text(lim: BotLimits) -> str:
    """Confirmation line with the book walk (RF-20, D-60), or nothing."""
    walk = [f"{NAMES[vid]} {pct} %" for vid, pct in lim.max_walk_pct.items() if pct > 0]
    if not walk:
        return ""
    return f"recorre el book hasta {' · '.join(walk)} del mejor precio\n"


def _move_text(lim: BotLimits) -> str:
    """Confirmation line with the sharp move filter (D-71), or nothing."""
    if lim.max_move_pct is None:
        return ""
    return (
        f"no dispara si el mid de un venue se movió más de {lim.max_move_pct} % "
        f"en {lim.move_window_ms / 1000:g} s\n"
    )


def _lead_text(lim: BotLimits) -> str:
    """Confirmation line with the first leg (D-75), or nothing."""
    if lim.lead_venue is None:
        return ""
    return (
        f"envía primero la pierna de {NAMES[lim.lead_venue]}; la otra solo por lo que "
        "llene (si no llena nada, no sale)\n"
    )


def _verdict(d: DoubleState, cancel_errors: dict[VenueId, str]) -> str | None:
    for leg in d.legs:
        if leg.status == "unknown":
            return f"uncertain: {leg.label}: {leg.error}"
    for leg in d.legs:
        if leg.status == "rejected":
            return f"rejected: {leg.label}: {leg.error or 'rechazada'}"
    if not d.done:
        open_legs = ", ".join(leg.label for leg in d.legs if not leg.done)
        errors = "; ".join(f"{NAMES[v]}: {e}" for v, e in cancel_errors.items())
        return f"unsettled: {open_legs} sin cerrar" + (f" ({errors})" if errors else "")
    if d.buy.filled != d.sell.filled:
        return (
            f"hanging_leg: compra {d.buy.filled} y venta {d.sell.filled} {BASE_LABEL} "
            f"en la doble {d.id}"
        )
    return None
