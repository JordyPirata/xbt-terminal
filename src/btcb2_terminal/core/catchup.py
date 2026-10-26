"""Poner el journal al día con los trades que el venue ya tenía al arrancar
(D-68): qué se sabe de cada trade por el journal y qué hay que apuntar.

Puro: las filas del journal entran ya leídas.

Spec: RF-11, RF-25 · design.md §6.5 · Tarea: T-68 · D-64, D-68
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .models import Fill, VenueId


@dataclass
class JournalIndex:
    """Lo que el journal (live) sabe de los trades y de las órdenes propias."""

    fees: dict[tuple[str, str], tuple[Decimal, str | None]] = field(default_factory=dict)
    ours: dict[tuple[str, str], str | None] = field(default_factory=dict)  # → client_id

    def fee(self, venue: VenueId, trade_id: str, fee: Decimal, asset: str | None) -> None:
        self.fees[(venue, trade_id)] = (fee, asset)


def index(rows: Iterable[Mapping[str, Any]]) -> JournalIndex:
    """En orden del journal: la última fee de cada trade (`fill` y luego `fill_fee`)
    y el `order_id` de cada `order_ack`, con su `client_id`. Solo filas de live."""
    out = JournalIndex()
    for row in rows:
        if row.get("mode") != "live":
            continue
        data, venue = row.get("data") or {}, row.get("venue")
        if not isinstance(data, Mapping) or not isinstance(venue, str):
            continue
        event = row.get("event")
        if event in ("fill", "fill_fee") and isinstance(data.get("trade_id"), str):
            try:
                fee = Decimal(str(data.get("fee")))
            except InvalidOperation:
                continue
            out.fee(venue, data["trade_id"], fee, data.get("fee_asset"))  # type: ignore[arg-type]
        elif event == "order_ack" and isinstance(data.get("order_id"), str):
            out.ours[(venue, data["order_id"])] = row.get("client_id")
    return out


Action = Literal["fee", "fill"] | None


def decide(fill: Fill, known: JournalIndex) -> tuple[Action, str | None]:
    """Qué apuntar de un trade que ya estaba: `fee` si el journal lo tiene con otra
    fee, `fill` si no lo tiene y es de una orden nuestra, None si nada. Con el
    `client_id` de la orden, si se conoce."""
    key = (fill.venue, fill.trade_id)
    client_id = known.ours.get((fill.venue, fill.order_id)) if fill.order_id else None
    if key in known.fees:
        if known.fees[key] != (fill.fee, fill.fee_asset):
            return "fee", client_id
        return None, client_id
    if fill.order_id is not None and (fill.venue, fill.order_id) in known.ours:
        return "fill", client_id
    return None, None
