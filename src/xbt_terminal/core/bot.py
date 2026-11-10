"""Taker arbitrage bot: limits, pure decision and state machine (phase 2).

`decide` looks at both routes ("buy on A, sell on B") with what the service gives
it: each venue's book, available balances and whether the venue is blocked (stale
book, clock skew, fault, uncertain send...). For each route (design.md §6.9):

    buy_price  = tick up (ref_A × (1 + slippage))
    sell_price = tick down (ref_B × (1 - slippage))
    net/unit   = sell_price × (1 - taker_B) - buy_price × (1 + taker_A)

The reference is the best ask/bid or, with the venue's `max_walk_pct` (D-60), any
level within that % of the best: a leg can take several steps of NonKYC's AMM pool
to reach the minimum. The reference pair with the highest net in BTC wins; not all
need trying (D-70, `_Pairs.search`): depth comes from cumulative sums and pairs are
pruned by the monotonicity of the net, with the same result as trying them all.

The net is pessimistic: at limit prices, not the best price, and with taker fee on
both legs. Its fee is `VenueView.net_fee` when the service sets it: that of a venue
paying in a token it holds (`token_fee`, D-63). The quantity is the minimum of the
book depth up to those prices on both sides, A's BTC (with its full fee), B's
BTCB2, `max_qty` and what is left of the daily cap, rounded down to both venues'
lots. `OrderService.prepare_double` applies the RF-10 cap afterwards.

With `max_move_pct` (D-71), it does not fire if a venue's mid moved more than that
% within `move_window_ms`: after a sweep, other bots take the price first and the
leg hangs.

With `[inventory]` (RF-26, D-46), a route does not fire either if the double would
leave a venue outside the BTCB2 % band and further from the target than before
(`inventory_veto`). The bot never buys to reach the target: it only vetoes.

States: `off → armed → firing → cooldown → armed`; any problem while firing →
`halted`, left only by hand (`arm`). No clock of its own: everything takes `now`
in ms.

Spec: RF-18 to RF-22, RF-24, RF-26, RNF-22, RNF-23 · design.md §6.9 · D-70, D-71 · Tasks:
T-34, T-35, T-41, T-49, T-57, T-72, T-73
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from .inventory import InventoryTarget, base_pct
from .models import BASE_LABEL, ZERO, Level, MarketSpec, VenueId

MIN_COOLDOWN_S = 2  # Neoxa: 10 orders every 10 s (RNF-03); a shot uses 1 + 1 cancel
MIN_FILL_TIMEOUT_MS = 100
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
HUNDRED = Decimal(100)
ROUTES: tuple[tuple[VenueId, VenueId], ...] = (("nk", "nx"), ("nx", "nk"))

BotStatus = Literal["off", "armed", "firing", "cooldown", "halted"]


@dataclass(frozen=True, slots=True)
class BotLimits:
    """`[bot]` section of config.toml, without `enabled`."""

    min_net_pct: Decimal  # minimum pessimistic net, % of the buy price (RF-19)
    max_slippage_pct: Decimal  # margin of the aggressive limits (RF-20)
    max_qty: Decimal  # BTCB2 per shot (RF-20)
    fill_timeout_ms: int  # the rest is cancelled after this (RF-20)
    cooldown_s: int  # between shots (RF-22)
    max_trades_per_hour: int
    max_daily_btc: Decimal  # buy notional sent per UTC day
    max_consecutive_failures: int
    # how far from the best price the reference level may be, per venue (RF-20, D-60)
    max_walk_pct: Mapping[VenueId, Decimal] = field(default_factory=dict)
    # maximum move of a venue's mid in the window; None = no filter (RF-19, D-71)
    max_move_pct: Decimal | None = None
    move_window_ms: int = 2000
    # venue of the first leg: the other goes out only for what it filled; None =
    # in parallel (RF-20, D-75)
    lead_venue: VenueId | None = None


@dataclass(frozen=True, slots=True)
class FeeToken:
    """Token a venue charges discounted fees in (`fee_token`, RF-24, D-63)."""

    asset: str
    min_available: Decimal  # `fee_token_min`
    discount_pct: Decimal  # `fee_discount_pct`


def token_fee(taker: Decimal, token: FeeToken, available: Decimal | None) -> Decimal | None:
    """Discounted taker fee with at least `min_available` of the token; otherwise,
    or with no balance read, None: the net uses the full fee."""
    if available is None or available < token.min_available:
        return None
    return taker * (1 - token.discount_pct / HUNDRED)


@dataclass(frozen=True, slots=True)
class BotState:
    status: BotStatus = "off"
    reason: str | None = None  # why it is halted
    fired_at: tuple[int, ...] = ()  # shots of the last hour
    day: int | None = None  # UTC day (ms // DAY_MS) that day_btc belongs to
    day_btc: Decimal = ZERO  # buy notional sent that day
    failures: int = 0  # consecutive rejected shots
    last_fire_at: int | None = None


@dataclass(frozen=True, slots=True)
class VenueView:
    """What the bot sees of a venue when deciding."""

    spec: MarketSpec
    bids: tuple[Level, ...]  # high to low
    asks: tuple[Level, ...]  # low to high
    quote_available: Decimal  # BTC
    base_available: Decimal  # BTCB2
    blocked: str | None = None  # reason not to trade this venue now
    # inventory ratio (RF-26): totals (available + in orders) and book mid
    quote_total: Decimal = ZERO
    base_total: Decimal = ZERO
    mid: Decimal | None = None
    # taker fee for the net (RF-19, D-63); None = the spec's. Quantity and inventory
    # keep the spec's
    net_fee: Decimal | None = None
    # how much the mid moved in `move_window_ms`; None = not measured (D-71)
    move_pct: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Fire:
    buy_venue: VenueId
    sell_venue: VenueId
    buy_price: Decimal
    sell_price: Decimal
    qty: Decimal
    net: Decimal  # BTC, pessimistic
    net_pct: Decimal  # of the buy notional

    @property
    def notional(self) -> Decimal:
        return self.qty * self.buy_price


@dataclass(frozen=True, slots=True)
class Skip:
    reason: str  # code: below_threshold, no_depth, no_balance, cooldown, ...
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _Miss:
    skip: Skip
    net_pct: Decimal | None  # to choose which reason to report




def _spent(state: BotState, now: int) -> Decimal:
    return state.day_btc if state.day == now // DAY_MS else ZERO


def _recent(state: BotState, now: int) -> tuple[int, ...]:
    return tuple(t for t in state.fired_at if now - t < HOUR_MS)


def price_move_pct(mids: Sequence[Decimal]) -> Decimal | None:
    """Move over a window of mids: (max - min) / min × 100; None without mids (D-71)."""
    if not mids:
        return None
    low, high = min(mids), max(mids)
    return (high - low) / low * HUNDRED


def _volatile(views: Mapping[VenueId, VenueView], limits: BotLimits) -> Skip | None:
    """The first venue, in order, whose mid moved more than `max_move_pct` in the
    window: after a jump, other bots get there first (D-71)."""
    cap = limits.max_move_pct
    if cap is None:
        return None
    for venue_id in sorted(views):
        move = views[venue_id].move_pct
        if move is not None and move > cap:
            window = limits.move_window_ms / 1000
            return Skip("volatile", f"{venue_id}: mid {move:.2f} % en {window:g} s > {cap} %")
    return None


def decide(
    views: Mapping[VenueId, VenueView],
    state: BotState,
    limits: BotLimits,
    now: int,
    target: InventoryTarget | None = None,
) -> Fire | Skip:
    if state.status != "armed":
        return Skip("not_armed", state.status)
    if state.last_fire_at is not None and now - state.last_fire_at < limits.cooldown_s * 1000:
        return Skip("cooldown")
    if len(_recent(state, now)) >= limits.max_trades_per_hour:
        return Skip("hour_cap", f"{limits.max_trades_per_hour} disparos en la última hora")
    budget = limits.max_daily_btc - _spent(state, now)
    if budget <= 0:
        return Skip("day_cap", f"{limits.max_daily_btc} BTC de compra hoy")
    if volatile := _volatile(views, limits):
        return volatile

    fires: list[Fire] = []
    misses: list[_Miss] = []
    for buy, sell in ROUTES:
        a, b = views.get(buy), views.get(sell)
        result = _route(a, b, buy, sell, limits, budget)
        if isinstance(result, Fire) and target is not None and a is not None and b is not None:
            veto = inventory_veto(a, b, result, target)
            if veto is not None:
                result = _Miss(Skip("inventory_band", veto), result.net_pct)
        if isinstance(result, Fire):
            fires.append(result)
        else:
            misses.append(result)
    if fires:
        return max(fires, key=lambda f: f.net)
    blocked = [m.skip for m in misses if m.skip.reason == "blocked"]
    priced = [m for m in misses if m.net_pct is not None]
    if priced:
        return max(priced, key=lambda m: m.net_pct or ZERO).skip
    return blocked[0] if blocked else misses[0].skip


def _route(
    a: VenueView | None,
    b: VenueView | None,
    buy: VenueId,
    sell: VenueId,
    limits: BotLimits,
    budget: Decimal,
) -> Fire | _Miss:
    for venue_id, v in ((buy, a), (sell, b)):
        if v is not None and v.blocked:
            return _Miss(Skip("blocked", f"{venue_id}: {v.blocked}"), None)
    if a is None or b is None or not a.asks or not b.bids:
        return _Miss(Skip("no_quotes", f"{buy}→{sell}: falta el ask o el bid"), None)

    walk_a = limits.max_walk_pct.get(buy, ZERO) / HUNDRED
    walk_b = limits.max_walk_pct.get(sell, ZERO) / HUNDRED
    refs_a = [
        price_level.price
        for price_level in a.asks
        if price_level.price <= a.asks[0].price * (1 + walk_a)
    ]
    refs_b = [
        price_level.price
        for price_level in b.bids
        if price_level.price >= b.bids[0].price * (1 - walk_b)
    ]
    asks, bids = _Side(a.asks, up=True), _Side(b.bids, up=False)
    # the first one, at the best prices, alone: without a cross nothing else is prepared
    first = _Pairs(a, b, buy, sell, limits, budget, refs_a[:1], refs_b[:1], asks, bids).pair(0, 0)
    if isinstance(first, _Miss) and first.skip.reason == "below_threshold":
        return first  # deeper levels only give a worse net
    r = _Pairs(a, b, buy, sell, limits, budget, refs_a, refs_b, asks, bids)
    if r.ordered:
        return r.search(first)
    # unsorted book: no monotonicity to prune with, so every pair is tried
    results = [first, *(r.pair(i, j) for i in range(r.n) for j in range(r.m) if i or j)]
    fires = [x for x in results if isinstance(x, Fire)]
    if fires:
        return max(fires, key=lambda f: f.net)
    # with depth that does not pay, the reason is the threshold, not the venue minimum
    short = [x for x in results if isinstance(x, _Miss) and x.skip.reason == "below_threshold"]
    return max(short, key=lambda m: m.net_pct or ZERO) if short else first


class _Side:
    """One side of the book with its cumulative depth (D-70): the quantity up to a
    price comes from `bisect` in O(log L), not from summing the whole side."""

    __slots__ = ("_cum", "_keys", "_levels", "_up", "ordered")

    def __init__(self, levels: tuple[Level, ...], up: bool) -> None:
        self._levels, self._up = levels, up  # up: asks, low to high
        prices = [price_level.price for price_level in levels]
        self.ordered = all((p <= q) if up else (p >= q) for p, q in pairwise(prices))
        self._keys = prices if up else [-p for p in prices]
        cum, acc = [ZERO], ZERO
        for price_level in levels:  # same summing order as before: same Decimal
            acc += price_level.qty
            cum.append(acc)
        self._cum = cum

    def depth(self, price: Decimal) -> Decimal:
        """What there is up to `price`: asks at or below it, bids at or above it."""
        if not self.ordered:
            ok = (lambda p: p <= price) if self._up else (lambda p: p >= price)
            return sum(
                (price_level.qty for price_level in self._levels if ok(price_level.price)), ZERO
            )
        key = price if self._up else -price
        return self._cum[bisect_right(self._keys, key)]


def _first(n: int, below: Callable[[int], bool]) -> int:
    """First index in [0, n) with `below` true, if `below` is monotonic
    (false… true); n if none."""
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if below(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


class _Pairs:
    """The reference pairs of a route (RF-20), with what does not depend on the pair
    computed once: limit prices, net per unit, depth and caps per row (buy price
    `i`) and per column (sell price `j`)."""

    def __init__(
        self,
        a: VenueView,
        b: VenueView,
        buy: VenueId,
        sell: VenueId,
        limits: BotLimits,
        budget: Decimal,
        refs_a: list[Decimal],
        refs_b: list[Decimal],
        asks: _Side,
        bids: _Side,
    ) -> None:
        self.a, self.b, self.buy, self.sell, self.limits = a, b, buy, sell, limits
        self.n, self.m = len(refs_a), len(refs_b)
        slip = limits.max_slippage_pct / HUNDRED
        fee_a = a.spec.taker_fee
        net_a = fee_a if a.net_fee is None else a.net_fee
        net_b = b.spec.taker_fee if b.net_fee is None else b.net_fee
        self.ordered = asks.ordered and bids.ordered
        self.buy_price = [a.spec.quantize_price(r * (1 + slip), "buy") for r in refs_a]
        self.sell_price = [b.spec.quantize_price(r * (1 - slip), "sell") for r in refs_b]
        self.buy_cost = [p * (1 + net_a) for p in self.buy_price]  # BTC per unit
        self.sell_gain = [p * (1 - net_b) for p in self.sell_price]
        self.depth_a = [asks.depth(p) for p in self.buy_price]
        self.depth_b = [bids.depth(p) for p in self.sell_price]
        self.balance = [
            min(a.quote_available / (p * (1 + fee_a)), b.base_available) for p in self.buy_price
        ]
        self.day = [budget / p for p in self.buy_price]

    def unit(self, i: int, j: int) -> Decimal:
        return self.sell_gain[j] - self.buy_cost[i]

    def net_pct(self, i: int, j: int) -> Decimal:
        return self.unit(i, j) / self.buy_price[i] * HUNDRED

    def below(self, i: int, j: int) -> bool:
        return self.net_pct(i, j) < self.limits.min_net_pct

    def pair(self, i: int, j: int) -> Fire | _Miss:
        """A route with one pair of reference prices (RF-20)."""
        a, b, buy, sell, limits = self.a, self.b, self.buy, self.sell, self.limits
        buy_price, sell_price = self.buy_price[i], self.sell_price[j]
        unit = self.unit(i, j)
        net_pct = unit / buy_price * HUNDRED
        if net_pct < limits.min_net_pct:
            detail = f"{buy}→{sell}: neta {net_pct:.2f} % < {limits.min_net_pct} %"
            return _Miss(Skip("below_threshold", detail), net_pct)

        terms = {
            "no_depth": min(self.depth_a[i], self.depth_b[j]),
            "no_balance": self.balance[i],
            "max_qty": limits.max_qty,
            "day_cap": self.day[i],
        }
        binding = min(terms, key=lambda k: terms[k])
        quantity = b.spec.quantize_qty(a.spec.quantize_qty(terms[binding]))
        if a.spec.quantize_qty(quantity) != quantity:  # lots that do not fit each other
            return _Miss(
                Skip("lot_mismatch", f"{quantity} no es múltiplo del lote de {buy}"), net_pct
            )
        if quantity <= 0 or quantity < max(a.spec.min_qty, b.spec.min_qty):
            detail = f"{buy}→{sell}: {terms[binding]:f} {BASE_LABEL} no llega al lote"
            return _Miss(Skip(binding, detail), net_pct)
        if (
            quantity * buy_price < a.spec.min_notional
            or quantity * sell_price < b.spec.min_notional
        ):
            detail = f"{buy}→{sell}: {quantity} {BASE_LABEL} bajo el mínimo"
            return _Miss(Skip("min_notional", detail), net_pct)
        return Fire(buy, sell, buy_price, sell_price, quantity, unit * quantity, net_pct)

    def _cap(self, i: int, jc: int) -> Decimal:
        """Upper bound of the quantity of row `i` over columns [0, jc)."""
        return min(
            self.depth_a[i], self.depth_b[jc - 1], self.balance[i], self.limits.max_qty,
            self.day[i],
        )  # fmt: skip

    def _can_fire(self, i: int, cap: Decimal) -> bool:
        """Can any pair in the row fire? Checked with the maximum quantity (quantity
        grows with the bound and lot rounding is monotonic)."""
        a, b = self.a.spec, self.b.spec
        top = b.quantize_qty(a.quantize_qty(cap))
        if top <= 0 or top < max(a.min_qty, b.min_qty):
            return False
        return top * self.buy_price[i] >= a.min_notional and (
            top * self.sell_price[0] >= b.min_notional
        )

    def search(self, first: Fire | _Miss) -> Fire | _Miss:
        """Same result as trying every pair, without trying them all (D-70). The net %
        falls as the buy price (`i`) rises and the sell price (`j`) falls, so:

        - a row from its first pair below the threshold, and all rows from the first
          that starts below it, only give `below_threshold` reasons with a lower or
          equal net (and later ones: on a tie the first wins);
        - a row whose maximum quantity reaches neither the lot nor the minimums does
          not fire;
        - a pair's net cannot exceed `unit(i, j) × row bound`: rows go from highest to
          lowest bound and stop when the bound cannot reach the best shot.
        """
        i_cut = _first(self.n, lambda i: self.below(i, 0))
        short: list[tuple[int, int]] = [(i_cut, 0)] if i_cut < self.n else []
        rows: list[tuple[Decimal, int, int, Decimal]] = []  # (bound, i, jc, quantity)
        for i in range(i_cut):
            jc = _first(self.m, lambda j, i=i: self.below(i, j))
            if jc < self.m:
                short.append((i, jc))
            cap = self._cap(i, jc)
            if self._can_fire(i, cap):
                unit = self.unit(i, 0)
                rows.append((unit * cap if unit > 0 else ZERO, i, jc, cap))
        rows.sort(key=lambda row: row[0], reverse=True)  # stable: on a tie, by i

        best: Fire | None = None
        best_at = (0, 0)
        for bound, i, jc, cap in rows:
            if best is not None and bound < best.net:
                break
            for j in range(jc):
                if best is not None:
                    unit = self.unit(i, j)
                    if (unit * cap if unit > 0 else ZERO) < best.net:
                        break  # the next columns have less net per unit
                got = self.pair(i, j)
                if isinstance(got, Fire) and (
                    best is None or got.net > best.net or (got.net == best.net and (i, j) < best_at)
                ):
                    best, best_at = got, (i, j)
        if best is not None:
            return best
        if not short:
            return first
        # the highest net; on a tie, the first in pair order
        return self.pair(*max(sorted(short), key=lambda ij: self.net_pct(*ij)))


def inventory_veto(a: VenueView, b: VenueView, fire: Fire, target: InventoryTarget) -> str | None:
    """Why the double worsens some venue's ratio (RF-26), or None. Buys on `a` and
    sells on `b`, with each one's taker fee; valued at the venue mid or, without
    one, at its leg's limit price."""
    quantity = fire.qty
    legs = (
        (
            fire.buy_venue,
            a,
            quantity,
            -quantity * fire.buy_price * (1 + a.spec.taker_fee),
            fire.buy_price,
        ),
        (
            fire.sell_venue,
            b,
            -quantity,
            quantity * fire.sell_price * (1 - b.spec.taker_fee),
            fire.sell_price,
        ),
    )
    for venue_id, v, d_base, d_quote, limit in legs:
        price = v.mid if v.mid is not None else limit
        before = base_pct(v.quote_total, v.base_total, price)
        after = base_pct(v.quote_total + d_quote, v.base_total + d_base, price)
        if before is None or after is None:
            continue  # venue with nothing to value
        if target.worsens(before, after):
            return (
                f"{venue_id}: {BASE_LABEL} {before:.1f} % → {after:.1f} %, fuera de "
                f"{target.low}-{target.high} %"
            )
    return None




def arm(state: BotState) -> BotState:
    """Arm by hand: also leaves `halted` (RF-21) and forgets the failures."""
    return replace(state, status="armed", reason=None, failures=0)


def disarm(state: BotState) -> BotState:
    return replace(state, status="off", reason=None)


def fired(state: BotState, fire: Fire, now: int) -> BotState:
    """Count the shot when sent, not when filled: the caps are on what was sent."""
    return replace(
        state,
        status="firing",
        fired_at=(*_recent(state, now), now),
        day=now // DAY_MS,
        day_btc=_spent(state, now) + fire.notional,
        last_fire_at=now,
    )


def finished(state: BotState, limits: BotLimits, now: int, *, error: str | None) -> BotState:
    """The shot's double ended. With `error` (hanging, uncertain, venue rejection,
    failed cancel): `halted` (RF-21). Without it, cool down, unless no daily cap
    is left (RF-22)."""
    if error is not None:
        return replace(state, status="halted", reason=error)
    if _spent(state, now) >= limits.max_daily_btc:
        return capped(state, limits)
    return replace(state, status="cooldown", failures=0)


def capped(state: BotState, limits: BotLimits) -> BotState:
    """Daily cap used up (RF-22): `halted`, only re-armed by hand. Used by `finished`
    and by `BotService` when `decide` gives `Skip("day_cap")`: cap already spent at
    evaluation (after re-arming or restarting the same day) or less than one lot of
    budget left (T-46, D-54)."""
    reason = f"day_cap: {limits.max_daily_btc} BTC de compra hoy"
    return replace(state, status="halted", reason=reason, failures=0)


def rejected(state: BotState, limits: BotLimits, reason: str) -> BotState:
    """The double was never sent (RiskCheck rejected it): nothing is open, so back to
    `armed`; after `max_consecutive_failures` in a row, `halted`."""
    failures = state.failures + 1
    if failures >= limits.max_consecutive_failures:
        msg = f"max_consecutive_failures: {failures} disparos rechazados seguidos ({reason})"
        return replace(state, status="halted", reason=msg, failures=failures)
    return replace(state, status="armed", failures=failures)


def tick(state: BotState, limits: BotLimits, now: int) -> BotState:
    """Time passing: end of the cooldown and forgetting shots older than 1 h."""
    state = replace(state, fired_at=_recent(state, now))
    if (
        state.status == "cooldown"
        and state.last_fire_at is not None
        and now - state.last_fire_at >= limits.cooldown_s * 1000
    ):
        return replace(state, status="armed")
    return state
