"""Book L2: snapshot + deltas, top-N con acumulado, mid.

Un snapshot reemplaza el book entero. Los deltas se aplican en orden de `seq`;
si llega uno que salta números, el book se marca inválido y deja de dar precios
(`mid` None → RiskCheck rechaza por `stale_data`) hasta el siguiente snapshot,
que MarketHub pide al ver `needs_snapshot`. Los venues sin `seq` aplican todo.

`ts` es la hora del exchange; `received_at`, la local de recepción que pasa quien
aplica (MarketHub). La frescura se mide con la local (RNF-16, D-30): con el reloj
del venue desfasado, `ts` diría que un book viejo es nuevo o al revés.

Spec: RF-02, RNF-16, RNF-23 · design.md §6.1 · D-70 · Tareas: T-10, T-29, T-72
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
        self.ts: int | None = None  # hora del exchange del último evento aplicado
        self.received_at: int | None = None  # hora local a la que llegó
        self.valid = False

    @property
    def needs_snapshot(self) -> bool:
        return not self.valid

    def apply(self, event: BookSnapshot | BookDelta, received_at: int | None = None) -> bool:
        """Aplica el evento. False si el book queda (o sigue) inválido. Sin
        `received_at`, se toma la hora del evento."""
        at = event.ts if received_at is None else received_at
        if event.venue != self.venue:
            raise ValueError(f"evento de {event.venue} aplicado al book de {self.venue}")

        if isinstance(event, BookSnapshot):
            self._bids = {lv.price: lv for lv in event.bids if lv.qty > 0}
            self._asks = {lv.price: lv for lv in event.asks if lv.qty > 0}
            self.seq, self.ts, self.valid = event.seq, event.ts, True
            self.received_at = at
            return True

        if not self.valid:
            return False
        if event.seq is not None and self.seq is not None:
            if event.seq <= self.seq:
                return True  # viejo o duplicado
            if event.seq != self.seq + 1:
                self.valid = False
                return False
        for side, levels in ((self._bids, event.bids), (self._asks, event.asks)):
            for lv in levels:
                if lv.qty > 0:
                    side[lv.price] = lv
                else:
                    side.pop(lv.price, None)
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
        """None si falta un lado o el book está cruzado."""
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

    def top(self, n: int) -> tuple[tuple[BookRow, ...], tuple[BookRow, ...]]:
        """N mejores niveles por lado (bids de mayor a menor, asks de menor a mayor)."""
        if not self.valid:
            return (), ()
        bids, asks = self._best(n)
        return _rows(bids), _rows(asks)

    def _best(self, n: int) -> tuple[list[Level], list[Level]]:
        """Los N mejores por lado sin ordenar el book entero (D-70): `heapq` da
        lo mismo que `sorted(...)[:n]`, y los precios no se repiten (son claves)."""
        bids = heapq.nlargest(n, self._bids.values(), key=_price)
        asks = heapq.nsmallest(n, self._asks.values(), key=_price)
        return bids, asks

    def snapshot(self, n: int) -> BookSnapshot | None:
        """Los N mejores niveles por lado como foto, para reenviar el book por el
        socket del daemon (D-66); None si el book no es válido."""
        if not self.valid or self.ts is None:
            return None
        bids, asks = self._best(n)
        return BookSnapshot(self.venue, tuple(bids), tuple(asks), self.ts, self.seq)


def _price(lv: Level) -> Decimal:
    return lv.price


def _rows(levels: list[Level]) -> tuple[BookRow, ...]:
    total = ZERO
    rows = []
    for lv in levels:
        total += lv.qty
        rows.append(BookRow(lv.price, lv.qty, total, lv.amm))
    return tuple(rows)
