"""Realized result of each double order, from the journal (RF-25).

Pure: takes journal rows already read and returns a `DoubleResult` per double, a
summary and the cumulative curve. Not a portfolio PnL: it values neither inventory
nor what was overfilled on a leg (D-44).

    order_sent(client_id, double, side) ─┐
    order_ack(client_id, order_id)       ├─▶ fills per leg ─▶ DoubleResult
    fill · fill_fee · bot_fire · ...  ───┘

A fill goes to its order by `client_id`, else by `order_id` (through `order_ack`)
and, if it has neither (Neoxa, D-45), to the only order of the same venue and side
sent before, with a price compatible with its limit and enough pending quantity;
if several fit, to the only one sent in the last minute. If still in doubt, it
stays unassigned and is counted.

Result = matched × (avg sell - avg buy) - fees in BTC, with matched = min(bought,
sold). A BTCB2 fee is valued at its fill's price; another token's at the price in
`prices` (the venue's fee token, D-65) and, without one, noted apart and not
subtracted. A `fill_fee` (the venue corrected the fee, D-64) replaces the fill's.

The `Report` also carries the account value series (RF-32, D-69).

Spec: RF-11, RF-24, RF-25, RF-32, CA-11 · design.md §6.10 · D-44, D-45, D-64,
D-65, D-69 · Tasks: T-40, T-59, T-60, T-69, T-97
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .models import ZERO, Side, VenueId
from .valuation import ValueSample, series

DAY_MS = 86_400_000
RECENT_MS = 60_000  # a bot take fills within seconds (D-45)
BASE = "BTCB2"
QUOTE = "BTC"

Origin = Literal["bot", "manual"]
Status = Literal["closed", "hanging", "no_fills"]
Range = Literal["dia", "7d", "todo"]
RANGES: tuple[Range, ...] = ("dia", "7d", "todo")
_FIELDS = ("ts", "mode", "event", "data")


def read_rows(lines: Iterable[str]) -> tuple[list[dict[str, Any]], int]:
    """JSON rows of the journal and how many could not be read (blank ones do not count)."""
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict) or any(k not in row for k in _FIELDS):
            bad += 1
            continue
        rows.append(row)
    return rows, bad


def range_start(rng: Range, now_ms: int) -> int | None:
    """Range start in ms: today's UTC day, the last 7 days or everything (None)."""
    today = now_ms // DAY_MS * DAY_MS
    if rng == "dia":
        return today
    if rng == "7d":
        return today - 6 * DAY_MS
    return None


@dataclass(slots=True)
class _Order:
    venue: VenueId
    side: Side
    qty: Decimal
    price: Decimal
    sent_at: int
    double: str | None
    filled: Decimal = ZERO
    rejected: bool = False

    def takes(self, venue: str, side: str, qty: Decimal, price: Decimal, at: int) -> bool:
        if self.rejected or self.venue != venue or self.side != side or self.sent_at > at:
            return False
        if self.filled + qty > self.qty:
            return False
        return price <= self.price if side == "buy" else price >= self.price


@dataclass(slots=True)
class _Leg:
    venue: VenueId | None = None
    filled: Decimal = ZERO
    cost: Decimal = ZERO  # Σ qty × price, in BTC


@dataclass(slots=True)
class _Double:
    id: str
    started: int
    buy: _Leg = field(default_factory=_Leg)
    sell: _Leg = field(default_factory=_Leg)
    fees: Decimal = ZERO
    other_fees: dict[str, Decimal] = field(default_factory=dict)
    expected_pct: Decimal | None = None
    hanging: bool = False


@dataclass(frozen=True, slots=True)
class DoubleResult:
    id: str
    origin: Origin
    started: int  # epoch ms of the first send
    buy_venue: VenueId | None
    sell_venue: VenueId | None
    buy_filled: Decimal
    sell_filled: Decimal
    buy_avg: Decimal | None
    sell_avg: Decimal | None
    matched: Decimal
    fees: Decimal  # BTC
    other_fees: tuple[tuple[str, Decimal], ...]
    mismatch: Decimal  # BTCB2: bought - sold
    status: Status
    result: Decimal | None  # BTC; closed doubles only
    result_pct: Decimal | None  # of what the matched part paid
    expected_pct: Decimal | None  # the bot's expected net
    # BTC: those of `other_fees` with a price, which also subtract from the result (D-65)
    token_fees: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class Summary:
    closed: int
    total: Decimal
    mean: Decimal | None
    best: Decimal | None
    worst: Decimal | None
    hanging: int
    no_fills: int
    unassigned: int  # fills that could not be assigned to an order
    bad_rows: int
    # fees of closed doubles in other assets without price (D-63): not subtracted
    other_fees: tuple[tuple[str, Decimal], ...] = ()
    # BTC prices used to value the token fees of closed doubles (D-65)
    prices: tuple[tuple[str, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class Report:
    doubles: tuple[DoubleResult, ...]
    summary: Summary
    curve: tuple[tuple[int, Decimal], ...]  # (double start, cumulative)
    # account value in the range (RF-32, D-69): `value` samples of the journal
    values: tuple[ValueSample, ...] = ()


def _part(order_leg: _Leg, qty: Decimal) -> Decimal:
    """What `qty` of the leg paid or collected, at its average price."""
    return order_leg.cost if qty == order_leg.filled else qty * order_leg.cost / order_leg.filled


def _add_fee(d: _Double, fee: Decimal, asset: str | None, price: Decimal) -> None:
    """Add (or subtract, with a negative fee) a fill's fee: in BTC, BTCB2 at its price;
    another asset's apart and unvalued."""
    if fee == 0 or asset in (None, QUOTE):
        d.fees += fee
    elif asset == BASE:
        d.fees += fee * price
    else:
        left = d.other_fees.get(asset, ZERO) + fee
        if left == 0:
            d.other_fees.pop(asset, None)
        else:
            d.other_fees[asset] = left


def _ms(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def _dec(value: Any) -> Decimal:
    out = Decimal(str(value))
    if not out.is_finite():
        raise ValueError(f"no es un número: {value!r}")
    return out


class _Builder:
    def __init__(self, mode: str = "live") -> None:
        self.mode = mode
        self.orders: dict[str, _Order] = {}
        # per (venue, side), the orders that can still take a fill of positive quantity:
        # not rejected and not full. Without it each id-less fill was compared with
        # every order of the range (T-97)
        self.open: dict[tuple[str, str], dict[str, None]] = {}
        self.acks: dict[tuple[str, str], str] = {}
        self.doubles: dict[str, _Double] = {}
        self.fired: dict[str, Decimal | None] = {}
        self.hanging: set[str] = set()
        self.trades: set[tuple[str, str]] = set()
        # fee written for each fill assigned to a double, for `fill_fee` (D-64)
        self.fill_fees: dict[tuple[str, str], tuple[_Double, Decimal, Decimal, str | None]] = {}
        self.unassigned = 0
        self.bad = 0

    def feed(self, row: Mapping[str, Any]) -> None:
        if row.get("mode") != self.mode:
            return
        event, data = row.get("event"), row.get("data") or {}
        handler = getattr(self, f"_on_{event}", None)
        if handler is None:
            return
        try:
            handler(row, data)
        except (ValueError, KeyError, TypeError, InvalidOperation, AttributeError):
            self.bad += 1  # an odd row does not stop computing the rest

    def _on_order_sent(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        cid, at, double = row["client_id"], _ms(row["ts"]), data.get("double")
        if cid is None or data["side"] not in ("buy", "sell"):
            raise ValueError("order_sent sin client_id o con lado raro")
        order = _Order(
            row["venue"], data["side"], _dec(data["qty"]), _dec(data["price"]), at, double
        )
        self._close(cid)
        self.orders[cid] = order
        if order.filled < order.qty:
            self.open.setdefault((order.venue, order.side), {})[cid] = None
        if double is not None:
            d = self.doubles.setdefault(double, _Double(double, at))
            getattr(d, order.side).venue = order.venue

    def _on_order_ack(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        if row["client_id"] is not None and data.get("order_id"):
            self.acks[(row["venue"], str(data["order_id"]))] = row["client_id"]
        if data.get("status") == "rejected":
            self._on_order_error(row, data)

    def _on_order_error(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        order = self.orders.get(row["client_id"])
        if order is not None:
            order.rejected = True
            self._close(row["client_id"])

    def _on_bot_fire(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        percent = data.get("net_pct")
        self.fired[data["double"]] = _dec(percent) if percent is not None else None

    def _on_hanging_leg(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        self.hanging.add(data["double"])

    def _on_fill(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        venue, side = row["venue"], data["side"]
        qty, price, at = _dec(data["qty"]), _dec(data["price"]), _ms(row["ts"])
        fee = _dec(data.get("fee") or 0)
        key = (venue, str(data["trade_id"]))
        if key in self.trades:
            return
        self.trades.add(key)
        cid = self._owner(row, data, venue, side, qty, price, at)
        order = self.orders.get(cid) if cid is not None else None
        if order is None:
            self.unassigned += 1
            return
        order.filled += qty
        if cid is not None and order.filled >= order.qty:
            self._close(cid)
        if order.double is None:
            return
        d = self.doubles[order.double]
        order_leg = getattr(d, order.side)
        order_leg.filled += qty
        order_leg.cost += qty * price
        asset = data.get("fee_asset")
        _add_fee(d, fee, asset, price)
        self.fill_fees[key] = (d, price, fee, asset)

    def _on_fill_fee(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        """The venue corrected a fill's fee (D-64): it replaces the written one. For a
        fill outside any double, there is nothing to correct."""
        key = (row["venue"], str(data["trade_id"]))
        known = self.fill_fees.get(key)
        if known is None:
            return
        d, price, old_fee, old_asset = known
        fee, asset = _dec(data.get("fee") or 0), data.get("fee_asset")
        _add_fee(d, -old_fee, old_asset, price)
        _add_fee(d, fee, asset, price)
        self.fill_fees[key] = (d, price, fee, asset)

    def _owner(
        self,
        row: Mapping[str, Any],
        data: Mapping[str, Any],
        venue: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        at: int,
    ) -> str | None:
        cid = row.get("client_id")
        if cid in self.orders:
            return cid
        oid = data.get("order_id")
        if oid and (venue, str(oid)) in self.acks:
            return self.acks[(venue, str(oid))]
        # no ids (Neoxa, D-45): the only compatible order, or the only recent one. A full
        # order can still take a zero or negative quantity: those look at all of them
        pool = self.open.get((venue, side), {}) if qty > 0 else self.orders
        fits = [c for c in pool if self.orders[c].takes(venue, side, qty, price, at)]
        if len(fits) > 1:
            fits = [c for c in fits if at - self.orders[c].sent_at <= RECENT_MS]
        return fits[0] if len(fits) == 1 else None

    def _close(self, cid: str) -> None:
        order = self.orders.get(cid)
        if order is not None:
            self.open.get((order.venue, order.side), {}).pop(cid, None)

    def result(self, d: _Double, prices: Mapping[str, Decimal]) -> DoubleResult:
        buy, sell = d.buy, d.sell
        token_fees = ZERO
        for asset, qty in d.other_fees.items():
            if asset in prices:
                token_fees += qty * prices[asset]
        buy_avg = buy.cost / buy.filled if buy.filled else None
        sell_avg = sell.cost / sell.filled if sell.filled else None
        matched = min(buy.filled, sell.filled)
        mismatch = buy.filled - sell.filled
        status: Status
        if buy_avg is None or sell_avg is None:
            status = "no_fills"
        elif d.id in self.hanging and mismatch != 0:
            status = "hanging"
        else:
            status = "closed"
        result = percent = None
        if status == "closed":
            # with the whole leg matched, its exact cost: the average carries a division
            paid, got = _part(buy, matched), _part(sell, matched)
            result = got - paid - d.fees - token_fees
            percent = result / paid * 100
        return DoubleResult(
            id=d.id,
            origin="bot" if d.id in self.fired else "manual",
            started=d.started,
            buy_venue=buy.venue,
            sell_venue=sell.venue,
            buy_filled=buy.filled,
            sell_filled=sell.filled,
            buy_avg=buy_avg,
            sell_avg=sell_avg,
            matched=matched,
            fees=d.fees,
            other_fees=tuple(sorted(d.other_fees.items())),
            mismatch=mismatch,
            status=status,
            result=result,
            result_pct=percent,
            expected_pct=self.fired.get(d.id),
            token_fees=token_fees,
        )


def compute(
    rows: Iterable[Mapping[str, Any]],
    *,
    since_ms: int | None = None,
    bad_lines: int = 0,
    prices: Mapping[str, Decimal] | None = None,
    mode: str = "live",
) -> Report:
    """Results of the doubles started since `since_ms` (all if None).

    Rows are processed in journal order; pass the ones before the range too, so fills
    are assigned as in the whole day. `prices`: BTC per unit of the fee tokens (D-65);
    without a price, the fee goes apart and is not subtracted. `mode`: which mode's
    rows count; a replay report reads the dry-run ones (RF-35)."""
    prices = prices or {}
    rows = list(rows)
    b = _Builder(mode)
    for row in rows:
        b.feed(row)
    values, bad_values = series(rows, since_ms)
    doubles = tuple(
        b.result(d, prices)
        for d in sorted(b.doubles.values(), key=lambda d: (d.started, d.id))
        if since_ms is None or d.started >= since_ms
    )
    closed = [d.result for d in doubles if d.result is not None]
    other: dict[str, Decimal] = {}
    used: dict[str, Decimal] = {}
    for d in doubles:
        if d.result is not None:
            for asset, qty in d.other_fees:
                if asset in prices:
                    used[asset] = prices[asset]
                else:
                    other[asset] = other.get(asset, ZERO) + qty
    curve: list[tuple[int, Decimal]] = []
    acc = ZERO
    for d in doubles:
        if d.result is not None:
            acc += d.result
            curve.append((d.started, acc))
    summary = Summary(
        closed=len(closed),
        total=sum(closed, ZERO),
        mean=sum(closed, ZERO) / len(closed) if closed else None,
        best=max(closed) if closed else None,
        worst=min(closed) if closed else None,
        hanging=sum(d.status == "hanging" for d in doubles),
        no_fills=sum(d.status == "no_fills" for d in doubles),
        unassigned=b.unassigned,
        bad_rows=b.bad + bad_lines + bad_values,
        other_fees=tuple(sorted(other.items())),
        prices=tuple(sorted(used.items())),
    )
    return Report(doubles, summary, tuple(curve), values)
