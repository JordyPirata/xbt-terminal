"""Qué órdenes del venue pueden ser la de un envío incierto (RNF-12, D-26).

Puro: recibe la petición, las órdenes recientes del venue, cuándo se envió y qué
órdenes ya se conocían al enviar. Con `by_client_id` (el venue devuelve el
client_id) solo vale la del mismo client_id. Sin él (Neoxa, PA-09), lado, precio
y cantidad iguales y creada a menos de `window_ms` del envío, en los dos sentidos
porque la hora es la del exchange. Una orden ya conocida nunca es candidata.

Spec: RNF-12 · design.md §6.2 · Tarea: T-25
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Order, OrderRequest

WINDOW_MS = 60_000


def candidates(
    req: OrderRequest,
    orders: Iterable[Order],
    sent_at: int,
    known: frozenset[str],
    *,
    by_client_id: bool,
    window_ms: int = WINDOW_MS,
) -> list[Order]:
    """Candidatas sin repetir; si una orden sale dos veces, vale la última."""
    found: dict[str, Order] = {}
    for o in orders:
        if o.venue != req.venue or o.order_id in known:
            continue
        if by_client_id:
            same = o.client_id == req.client_id
        else:
            same = (o.side, o.price, o.qty) == (req.side, req.price, req.qty) and abs(
                o.ts - sent_at
            ) <= window_ms
        if same:
            found.pop(o.order_id, None)
            found[o.order_id] = o
    return list(found.values())
