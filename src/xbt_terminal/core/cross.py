"""Cross panel: mid, inner spread and difference between venues, gross and net.

All in BTC per 1 BTCB2 (RNF-10: both venues quote against BTC, no conversion). For
each executable direction "buy on A, sell on B":

    gross = bid_B - ask_A
    net   = bid_B × (1 - taker_B) - ask_A × (1 + taker_A)

with the % over ask_A. Taker fees are used because the route crosses both books. A
route only needs the ask where it buys and the bid where it sells; a venue's stats,
instead, need both sides and an uncrossed book.

Spec: RF-04 · design.md §6.6 · Task: T-14a
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import VenueId

HUNDRED = Decimal(100)
SATS_PER_BTC = Decimal(100_000_000)


@dataclass(frozen=True, slots=True)
class Quote:
    venue: VenueId
    best_bid: Decimal | None
    best_ask: Decimal | None
    taker_fee: Decimal


@dataclass(frozen=True, slots=True)
class VenueStats:
    mid: Decimal
    spread: Decimal
    spread_pct: Decimal  # of the mid


@dataclass(frozen=True, slots=True)
class Route:
    buy_venue: VenueId
    sell_venue: VenueId
    gross: Decimal
    net: Decimal
    gross_pct: Decimal  # of the buy ask
    net_pct: Decimal


@dataclass(frozen=True, slots=True)
class CrossView:
    stats: dict[VenueId, VenueStats | None]
    mid_diff: Decimal | None  # mid_b - mid_a
    mid_diff_pct: Decimal | None  # of mid_a
    routes: tuple[Route | None, Route | None]  # a→b, b→a

    def route(self, buy: VenueId, sell: VenueId) -> Route | None:
        for candidate_route in self.routes:
            if candidate_route is not None and (
                candidate_route.buy_venue,
                candidate_route.sell_venue,
            ) == (buy, sell):
                return candidate_route
        return None


def sats(btc: Decimal) -> Decimal:
    return btc * SATS_PER_BTC


def venue_stats(quote: Quote) -> VenueStats | None:
    """None if a side is missing or the book is crossed: no reliable price."""
    if quote.best_bid is None or quote.best_ask is None or quote.best_bid >= quote.best_ask:
        return None
    mid = (quote.best_bid + quote.best_ask) / 2
    spread = quote.best_ask - quote.best_bid
    return VenueStats(mid=mid, spread=spread, spread_pct=spread / mid * HUNDRED)


def _route(buy: Quote, sell: Quote) -> Route | None:
    if buy.best_ask is None or sell.best_bid is None:
        return None
    gross = sell.best_bid - buy.best_ask
    net = sell.best_bid * (1 - sell.taker_fee) - buy.best_ask * (1 + buy.taker_fee)
    return Route(
        buy_venue=buy.venue,
        sell_venue=sell.venue,
        gross=gross,
        net=net,
        gross_pct=gross / buy.best_ask * HUNDRED,
        net_pct=net / buy.best_ask * HUNDRED,
    )


def cross(left_quote: Quote, right_quote: Quote) -> CrossView:
    if left_quote.venue == right_quote.venue:
        raise ValueError(f"panel cruzado con el mismo venue en los dos lados: {left_quote.venue}")
    sa, sb = venue_stats(left_quote), venue_stats(right_quote)
    mid_diff = sb.mid - sa.mid if sa and sb else None
    return CrossView(
        stats={left_quote.venue: sa, right_quote.venue: sb},
        mid_diff=mid_diff,
        mid_diff_pct=mid_diff / sa.mid * HUNDRED if mid_diff is not None and sa else None,
        routes=(_route(left_quote, right_quote), _route(right_quote, left_quote)),
    )
