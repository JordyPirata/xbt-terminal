"""Frozen oracle of the bot's decision (T-72, D-70): `decide`, `_route`, `_pair` and
`inventory_veto` as they were before lowering their complexity.

Not to be touched. `test_bot_equivalence.py` compares `core/bot.py`'s decision with
this one over thousands of cases: a bot behaviour change must be on purpose, with
its decision, and update this file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from xbt_terminal.core.bot import (
    DAY_MS,
    HOUR_MS,
    HUNDRED,
    ROUTES,
    BotLimits,
    BotState,
    Fire,
    Skip,
    VenueView,
)
from xbt_terminal.core.inventory import InventoryTarget, base_pct
from xbt_terminal.core.models import BASE_LABEL, ZERO, VenueId


@dataclass(frozen=True, slots=True)
class _Miss:
    skip: Skip
    net_pct: Decimal | None  # to choose which reason to report




def _spent(state: BotState, now: int) -> Decimal:
    return state.day_btc if state.day == now // DAY_MS else ZERO


def _recent(state: BotState, now: int) -> tuple[int, ...]:
    return tuple(t for t in state.fired_at if now - t < HOUR_MS)


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
    pairs = [(ra, rb) for ra in refs_a for rb in refs_b]  # the first one, at the best prices
    first = _pair(a, b, buy, sell, limits, budget, *pairs[0])
    if isinstance(first, _Miss) and first.skip.reason == "below_threshold":
        return first  # deeper levels only give a worse net
    results = [first, *(_pair(a, b, buy, sell, limits, budget, *p) for p in pairs[1:])]
    fires = [r for r in results if isinstance(r, Fire)]
    if fires:
        return max(fires, key=lambda f: f.net)
    # with depth that does not pay, the reason is the threshold, not the venue minimum
    short = [r for r in results if isinstance(r, _Miss) and r.skip.reason == "below_threshold"]
    return max(short, key=lambda m: m.net_pct or ZERO) if short else first


def _pair(
    a: VenueView,
    b: VenueView,
    buy: VenueId,
    sell: VenueId,
    limits: BotLimits,
    budget: Decimal,
    ref_a: Decimal,
    ref_b: Decimal,
) -> Fire | _Miss:
    """A route with one pair of reference prices (RF-20)."""
    slip = limits.max_slippage_pct / HUNDRED
    buy_price = a.spec.quantize_price(ref_a * (1 + slip), "buy")
    sell_price = b.spec.quantize_price(ref_b * (1 - slip), "sell")
    fee_a, fee_b = a.spec.taker_fee, b.spec.taker_fee
    net_a = fee_a if a.net_fee is None else a.net_fee
    net_b = fee_b if b.net_fee is None else b.net_fee
    unit = sell_price * (1 - net_b) - buy_price * (1 + net_a)
    net_pct = unit / buy_price * HUNDRED
    if net_pct < limits.min_net_pct:
        detail = f"{buy}→{sell}: neta {net_pct:.2f} % < {limits.min_net_pct} %"
        return _Miss(Skip("below_threshold", detail), net_pct)

    terms = {
        "no_depth": min(
            sum(
                (price_level.qty for price_level in a.asks if price_level.price <= buy_price),
                ZERO,
            ),
            sum(
                (price_level.qty for price_level in b.bids if price_level.price >= sell_price),
                ZERO,
            ),
        ),
        "no_balance": min(a.quote_available / (buy_price * (1 + fee_a)), b.base_available),
        "max_qty": limits.max_qty,
        "day_cap": budget / buy_price,
    }
    binding = min(terms, key=lambda k: terms[k])
    quantity = b.spec.quantize_qty(a.spec.quantize_qty(terms[binding]))
    if a.spec.quantize_qty(quantity) != quantity:  # lots that do not fit each other
        return _Miss(Skip("lot_mismatch", f"{quantity} no es múltiplo del lote de {buy}"), net_pct)
    if quantity <= 0 or quantity < max(a.spec.min_qty, b.spec.min_qty):
        detail = f"{buy}→{sell}: {terms[binding]:f} {BASE_LABEL} no llega al lote"
        return _Miss(Skip(binding, detail), net_pct)
    if quantity * buy_price < a.spec.min_notional or quantity * sell_price < b.spec.min_notional:
        detail = f"{buy}→{sell}: {quantity} {BASE_LABEL} bajo el mínimo"
        return _Miss(Skip("min_notional", detail), net_pct)
    return Fire(buy, sell, buy_price, sell_price, quantity, unit * quantity, net_pct)


def inventory_veto(a: VenueView, b: VenueView, fire: Fire, target: InventoryTarget) -> str | None:
    """Why the double worsens some venue's ratio (RF-26), or None. Buys on `a` and sells
    on `b`, with each one's taker fee; valued at the venue mid or, without one, at its
    leg's limit price."""
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
