"""L2 book: snapshot + deltas, top-N with cumulative size, mid.

A snapshot replaces the whole book. Deltas apply in `seq` order; one that skips
numbers marks the book invalid and it stops giving prices (`mid` None → RiskCheck
rejects with `stale_data`) until the next snapshot, which MarketHub requests on
seeing `needs_snapshot`. Venues without `seq` apply everything.

`ts` is the exchange time; `received_at` is the local arrival time passed by the
caller (MarketHub). Freshness uses the local one (RNF-16, D-30): with a skewed
venue clock, `ts` would call an old book new or the other way round.

Spec: RF-02, RNF-16, RNF-23 · design.md §6.1 · D-70 · Tasks: T-10, T-29, T-72, T-92
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from decimal import Decimal

from .models import ZERO, BookDelta, BookSnapshot, Level, VenueId


@dataclass(frozen=True, slots=True)
class BookRow:
    price: Decimal
    qty: Decimal
    cumulative: Decimal
    amm: bool


class L2Book:
    def __init__(self, venue: VenueId) -> None:
        self.venue = venue
        self._bids: dict[Decimal, Level] = {}
        self._asks: dict[Decimal, Level] = {}
        self.seq: int | None = None
        self.ts: int | None = None  # exchange time of the last applied event
        self.received_at: int | None = None  # local arrival time
        self.valid = False

    @property
    def needs_snapshot(self) -> bool:
        return not self.valid

    def apply(self, event: BookSnapshot | BookDelta, received_at: int | None = None) -> bool:
        """Apply the event. False if the book is (or stays) invalid. Without
        `received_at`, the event time is used."""
        at = event.ts if received_at is None else received_at
        if event.venue != self.venue:
            raise ValueError(f"evento de {event.venue} aplicado al book de {self.venue}")

        if isinstance(event, BookSnapshot):
            self._bids = {
                price_level.price: price_level for price_level in event.bids if price_level.qty > 0
            }
            self._asks = {
                price_level.price: price_level for price_level in event.asks if price_level.qty > 0
            }
            self.seq, self.ts, self.valid = event.seq, event.ts, True
            self.received_at = at
            return True

        if not self.valid:
            return False
        if event.seq is not None and self.seq is not None:
            if event.seq <= self.seq:
                return True
            if event.seq != self.seq + 1:
                self.valid = False
                return False
        for side, levels in ((self._bids, event.bids), (self._asks, event.asks)):
            for price_level in levels:
                if price_level.qty > 0:
                    side[price_level.price] = price_level
                else:
                    side.pop(price_level.price, None)
        self.seq = event.seq if event.seq is not None else self.seq
        self.ts, self.received_at = event.ts, at
        return True

    @property
    def best_bid(self) -> Decimal | None:
        return max(self._bids) if self.valid and self._bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return min(self._asks) if self.valid and self._asks else None

    @property
    def mid(self) -> Decimal | None:
        """None if a side is missing or the book is crossed."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None or bid >= ask:
            return None
        return (bid + ask) / 2

    @property
    def spread(self) -> Decimal | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None or bid >= ask:
            return None
        return ask - bid

    @property
    def depth(self) -> tuple[int, int]:
        return len(self._bids), len(self._asks)

    def top(self, count: int) -> tuple[tuple[BookRow, ...], tuple[BookRow, ...]]:
        """Best N levels per side (bids high to low, asks low to high)."""
        if not self.valid:
            return (), ()
        bids, asks = self._best(count)
        return _rows(bids), _rows(asks)

    def best_levels(self, count: int) -> tuple[tuple[Level, ...], tuple[Level, ...]]:
        """Best N levels per side as the book holds them, without the cumulative: the bot
        reads one per book change, and copying them cost more than deciding (T-92)."""
        if not self.valid:
            return (), ()
        bids, asks = self._best(count)
        return tuple(bids), tuple(asks)

    def _best(self, count: int) -> tuple[list[Level], list[Level]]:
        """Best N per side without sorting the whole book (D-70): `heapq` gives the
        same as `sorted(...)[:n]`, and prices do not repeat (they are keys)."""
        bids = heapq.nlargest(count, self._bids.values(), key=_price)
        asks = heapq.nsmallest(count, self._asks.values(), key=_price)
        return bids, asks

    def snapshot(self, count: int) -> BookSnapshot | None:
        """Best N levels per side as a snapshot, to forward the book over the
        daemon socket (D-66); None if the book is invalid."""
        if not self.valid or self.ts is None:
            return None
        bids, asks = self._best(count)
        return BookSnapshot(self.venue, tuple(bids), tuple(asks), self.ts, self.seq)


def _price(price_level: Level) -> Decimal:
    return price_level.price


def _rows(levels: list[Level]) -> tuple[BookRow, ...]:
    total = ZERO
    rows = []
    for price_level in levels:
        total += price_level.qty
        rows.append(BookRow(price_level.price, price_level.qty, total, price_level.amm))
    return tuple(rows)
