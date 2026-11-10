"""Which venue orders may be the one of an uncertain send (RNF-12, D-26).

Pure: takes the request, the venue's recent orders, when it was sent and which
orders were already known then. With `by_client_id` (the venue returns the
client_id) only the same client_id counts. Without it (Neoxa, PA-09), same side,
price and quantity, created within `window_ms` of the send, in both directions
because the time is the exchange's. An already known order is never a candidate.

Spec: RNF-12 · design.md §6.2 · Task: T-25
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Order, OrderRequest

WINDOW_MS = 60_000


def candidates(
    order_request: OrderRequest,
    orders: Iterable[Order],
    sent_at: int,
    known: frozenset[str],
    *,
    by_client_id: bool,
    window_ms: int = WINDOW_MS,
) -> list[Order]:
    """Candidates without repeats; if an order appears twice, the last one counts."""
    found: dict[str, Order] = {}
    for o in orders:
        if o.venue != order_request.venue or o.order_id in known:
            continue
        if by_client_id:
            same = o.client_id == order_request.client_id
        else:
            same = (o.side, o.price, o.qty) == (
                order_request.side,
                order_request.price,
                order_request.qty,
            ) and abs(o.ts - sent_at) <= window_ms
        if same:
            found.pop(o.order_id, None)
            found[o.order_id] = o
    return list(found.values())
