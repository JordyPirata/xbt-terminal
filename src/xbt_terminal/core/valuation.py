"""Account value over time (RF-32, D-69).

Pure: builds the sample the daemon writes as a `value` event from the RF-05
inventory, and reads a range's series from the journal with its summary. Not a
PnL: deposits and withdrawals show as jumps, unadjusted.

    inventory() ─▶ sample() ─▶ journal "value" ─▶ series() ─▶ summarize()

With self-custody wallets (RF-39, D-84) the total includes them and the sample carries
a `custody` entry; samples from before, without it, still read.

Spec: RF-05, RF-32, RF-31, RF-39, RNF-10, CA-17, CA-22 · design.md §6.5
Decisions: D-69, D-84, D-87 · Tasks: T-69, T-105, T-110
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .inventory import Inventory
from .models import ZERO, VenueId

HUNDRED = Decimal(100)
# a bigger jump between two samples in a row is a transfer, not the market (D-69)
JUMP_PCT = Decimal(5)


@dataclass(frozen=True, slots=True)
class VenueValue:
    venue: VenueId
    btc: Decimal
    btcb2: Decimal
    mid: Decimal
    value: Decimal  # BTC


@dataclass(frozen=True, slots=True)
class CustodyValue:
    """The self-custody wallets in a sample (RF-39)."""

    btc: Decimal
    btcb2: Decimal
    mid: Decimal  # mean of the venue mids
    value: Decimal  # BTC


@dataclass(frozen=True, slots=True)
class ValueSample:
    ts: int  # epoch ms of the interval multiple
    value: Decimal  # BTC, both venues plus self-custody if any
    every_ms: int  # sampling interval: decides what a gap is
    venues: tuple[VenueValue, ...]
    custody: CustodyValue | None = None


@dataclass(frozen=True, slots=True)
class ValueSummary:
    since: int
    until: int
    first: Decimal
    last: Decimal
    change: Decimal
    change_pct: Decimal | None  # of the first; None if that was 0
    high: Decimal
    low: Decimal
    count: int


def sample(
    inv: Inventory, mids: Mapping[VenueId, Decimal | None], timestamp: int, every_ms: int
) -> ValueSample | None:
    """The inventory sample, or None if a venue lacks its balance or mid: a partial
    total would mislead (like RF-05)."""
    if inv.value is None:
        return None
    venues = []
    for venue_id, item in sorted(inv.venues.items()):
        mid = mids.get(venue_id)
        if item is None or item.value is None or mid is None:
            return None
        venues.append(VenueValue(venue_id, item.btc, item.btcb2, mid, item.value))
    custody: CustodyValue | None = None
    if inv.custody is not None:
        if inv.custody.value is None or inv.custody_mid is None:
            return None
        custody = CustodyValue(
            inv.custody.btc, inv.custody.btcb2, inv.custody_mid, inv.custody.value
        )
    return ValueSample(timestamp, inv.value, every_ms, tuple(venues), custody)


def sample_data(sample: ValueSample) -> dict[str, Any]:
    """The `data` of the journal's `value` event."""
    custody = sample.custody
    extra = (
        {}
        if custody is None
        else {
            "custody": {
                "btc": custody.btc,
                "btcb2": custody.btcb2,
                "mid": custody.mid,
                "value": custody.value,
            }
        }
    )
    return {
        **extra,
        "at": sample.ts,
        "every_ms": sample.every_ms,
        "value": sample.value,
        "venues": {
            venue_value.venue: {
                "btc": venue_value.btc,
                "btcb2": venue_value.btcb2,
                "mid": venue_value.mid,
                "value": venue_value.value,
            }
            for venue_value in sample.venues
        },
    }


def _dec(value: Any) -> Decimal:
    out = Decimal(str(value))
    if not out.is_finite():
        raise ValueError(f"no es un número: {value!r}")
    return out


def _ms(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def _parse(row: Mapping[str, Any]) -> ValueSample:
    data = row["data"]
    venues = tuple(
        VenueValue(
            venue_id,
            _dec(entry["btc"]),
            _dec(entry["btcb2"]),
            _dec(entry["mid"]),
            _dec(entry["value"]),
        )
        for venue_id, entry in sorted(data["venues"].items())
    )
    at = data.get("at")
    timestamp = at if isinstance(at, int) and not isinstance(at, bool) else _ms(row["ts"])
    entry = data.get("custody")
    custody = (
        CustodyValue(
            _dec(entry["btc"]), _dec(entry["btcb2"]), _dec(entry["mid"]), _dec(entry["value"])
        )
        if entry is not None
        else None
    )
    return ValueSample(timestamp, _dec(data["value"]), int(data["every_ms"]), venues, custody)


def series(
    rows: Iterable[Mapping[str, Any]], since_ms: int | None
) -> tuple[tuple[ValueSample, ...], int]:
    """Samples since `since_ms` (all if None), by time, and how many `value` rows could
    not be read. The mode does not matter: balances are real in dry-run too (D-69)."""
    out: list[ValueSample] = []
    bad = 0
    for row in rows:
        if row.get("event") != "value":
            continue
        try:
            sample = _parse(row)
        except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
            bad += 1
            continue
        if since_ms is None or sample.ts >= since_ms:
            out.append(sample)
    out.sort(key=lambda sample: sample.ts)
    return tuple(out), bad


def summarize(values: Sequence[ValueSample], count: int = 0) -> ValueSummary | None:
    """`count`: the real number of samples when `values` is the reduced series (D-87)."""
    if not values:
        return None
    first, last = values[0].value, values[-1].value
    nums = [sample.value for sample in values]
    return ValueSummary(
        since=values[0].ts,
        until=values[-1].ts,
        first=first,
        last=last,
        change=last - first,
        change_pct=(last - first) / first * HUNDRED if first != ZERO else None,
        high=max(nums),
        low=min(nums),
        count=count or len(values),
    )


def segments(values: Sequence[ValueSample]) -> list[tuple[ValueSample, ...]]:
    """Stretches without gaps: cut where more than two intervals pass between samples
    (the daemon was stopped); gaps are not interpolated."""
    out: list[list[ValueSample]] = []
    for sample in values:
        if out and sample.ts - out[-1][-1].ts <= 2 * sample.every_ms:
            out[-1].append(sample)
        else:
            out.append([sample])
    return [tuple(seg) for seg in out]


def thin(values: Sequence[ValueSample], limit: int) -> tuple[ValueSample, ...]:
    """At most `n` samples spread out, with the first and last: plotext cannot tell
    more points than columns."""
    if len(values) <= limit:
        return tuple(values)
    last = len(values) - 1
    picks = sorted({round(i * last / (limit - 1)) for i in range(limit)})
    return tuple(values[i] for i in picks)


def _jumped(before: Decimal, after: Decimal) -> bool:
    if before == ZERO:
        return after != ZERO
    return abs(after - before) / before * HUNDRED > JUMP_PCT


def _transfer_between(prev: ValueSample, cur: ValueSample) -> bool:
    """A jump above `JUMP_PCT` between two samples in a row, in the total, a venue or
    the wallets."""
    pairs = [(prev.value, cur.value)]
    before = {venue_value.venue: venue_value.value for venue_value in prev.venues}
    pairs += [
        (before[venue_value.venue], venue_value.value)
        for venue_value in cur.venues
        if venue_value.venue in before
    ]
    if prev.custody is not None and cur.custody is not None:
        pairs.append((prev.custody.value, cur.custody.value))
    return any(_jumped(earlier, later) for earlier, later in pairs)


def last_stretch(values: Sequence[ValueSample]) -> tuple[ValueSample, ...]:
    """Samples since the last jump above `JUMP_PCT` between two in a row, in the total
    or a venue: a transfer in transit crushes the scale and hides what price and bot
    move (D-69)."""
    start = 0
    for i in range(1, len(values)):
        if _transfer_between(values[i - 1], values[i]):
            start = i
    return tuple(values[start:])


def decimate(values: Sequence[ValueSample], limit: int) -> tuple[ValueSample, ...]:
    """About `limit` samples at most for the wire (D-87): the whole range would be
    megabytes. Unlike `thin` it keeps what the summary and `z` read: first and last, both
    sides of every gap and jump, and the highest and lowest total of each bucket."""
    if len(values) <= limit:
        return tuple(values)
    keep = {0, len(values) - 1}
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        if cur.ts - prev.ts > 2 * cur.every_ms or _transfer_between(prev, cur):
            keep |= {i - 1, i}
    buckets = max(1, (limit - len(keep)) // 4)
    size = -(-len(values) // buckets)
    for start in range(0, len(values), size):
        chunk = range(start, min(start + size, len(values)))
        keep |= {
            chunk[0],
            chunk[-1],
            max(chunk, key=lambda i: values[i].value),
            min(chunk, key=lambda i: values[i].value),
        }
    return tuple(values[i] for i in sorted(keep))
