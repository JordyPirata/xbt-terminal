"""Panel cruzado: mid, spread interno y diferencia entre venues, bruta y neta.

Todo en BTC por 1 BTCB2 (RNF-10: ambos venues cotizan contra BTC, sin
conversión). Para cada dirección ejecutable "comprar en A, vender en B":

    bruto = bid_B - ask_A
    neto  = bid_B × (1 - taker_B) - ask_A × (1 + taker_A)

con los % sobre ask_A. Se usan fees taker porque la ruta cruza ambos books.
Una ruta solo necesita el ask de donde se compra y el bid de donde se vende;
las stats de un venue, en cambio, exigen los dos lados y un book sin cruzar.

Spec: RF-04 · design.md §6.6 · Tarea: T-14a
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
    spread_pct: Decimal  # sobre el mid


@dataclass(frozen=True, slots=True)
class Route:
    buy_venue: VenueId
    sell_venue: VenueId
    gross: Decimal
    net: Decimal
    gross_pct: Decimal  # sobre el ask de compra
    net_pct: Decimal


@dataclass(frozen=True, slots=True)
class CrossView:
    stats: dict[VenueId, VenueStats | None]
    mid_diff: Decimal | None  # mid_b - mid_a
    mid_diff_pct: Decimal | None  # sobre mid_a
    routes: tuple[Route | None, Route | None]  # a→b, b→a

    def route(self, buy: VenueId, sell: VenueId) -> Route | None:
        for r in self.routes:
            if r is not None and (r.buy_venue, r.sell_venue) == (buy, sell):
                return r
        return None


def sats(btc: Decimal) -> Decimal:
    return btc * SATS_PER_BTC


def venue_stats(q: Quote) -> VenueStats | None:
    """None si falta un lado o el book está cruzado: no hay precio fiable."""
    if q.best_bid is None or q.best_ask is None or q.best_bid >= q.best_ask:
        return None
    mid = (q.best_bid + q.best_ask) / 2
    spread = q.best_ask - q.best_bid
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


def cross(a: Quote, b: Quote) -> CrossView:
    if a.venue == b.venue:
        raise ValueError(f"panel cruzado con el mismo venue en los dos lados: {a.venue}")
    sa, sb = venue_stats(a), venue_stats(b)
    mid_diff = sb.mid - sa.mid if sa and sb else None
    return CrossView(
        stats={a.venue: sa, b.venue: sb},
        mid_diff=mid_diff,
        mid_diff_pct=mid_diff / sa.mid * HUNDRED if mid_diff is not None and sa else None,
        routes=(_route(a, b), _route(b, a)),
    )
