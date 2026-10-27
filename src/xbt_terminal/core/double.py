"""Orden doble: estado de las dos piernas y pierna colgada (RF-07, PA-08, D-15).

Puro: recibe órdenes y la hora, y devuelve estados nuevos. Una pierna se
identifica primero por su `client_id` (la respuesta del envío) y después por
`(venue, order_id)`, porque Neoxa no guarda el client_id.

Pierna colgada (supuesto de PA-08, generalizado en D-15): las cantidades llenas
difieren y la pierna que va por detrás está rechazada o cancelada, o el
desequilibrio dura más de `hanging_leg_after_s`. El reloj del desequilibrio
empieza cuando las cantidades dejan de coincidir y se reinicia si se igualan.
No se hace nada automático: solo se avisa.

Una pierna cuyo envío quedó incierto (RNF-12) está `unknown` hasta que se
reconcilia: si aparece, sigue como cualquier otra; si no existe, `rejected`.

En la doble del bot con `lead_venue` (D-75), la otra pierna está `held`
(preparada, sin enviar) hasta que la primera responde: se envía por lo que
llenó (`resize`) o pasa a `skipped`, que cuenta como cerrada con 0 lleno.

Spec: RF-07, RF-20, CA-03, RNF-12 · design.md §6.4, §6.9 · Tareas: T-18, T-25, T-77
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from .models import BASE_LABEL, ZERO, Order, OrderRequest, OrderStatus, Side

# unknown: envío incierto (RNF-12); held y skipped: la otra pierna del bot (D-75)
LegStatus = OrderStatus | Literal["unknown", "held", "skipped"]

NAMES = {"nk": "NonKYC", "nx": "Neoxa"}
SIDES = {"buy": "compra", "sell": "venta"}
ENDED = {"rejected": "rechazada", "cancelled": "cancelada", "skipped": "sin enviar"}


@dataclass(frozen=True, slots=True)
class Leg:
    req: OrderRequest
    status: LegStatus = "pending"
    order_id: str | None = None
    filled: Decimal = ZERO
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.status in ("filled", "cancelled", "rejected", "skipped")

    @property
    def label(self) -> str:
        return f"{SIDES[self.req.side]} en {NAMES[self.req.venue]}"

    def matches(self, order: Order) -> bool:
        if order.venue != self.req.venue:
            return False
        if self.order_id is not None:
            return order.order_id == self.order_id
        return order.client_id == self.req.client_id


@dataclass(frozen=True, slots=True)
class DoubleState:
    id: str
    buy: Leg
    sell: Leg
    created: int
    imbalance_since: int | None = None

    @property
    def legs(self) -> tuple[Leg, Leg]:
        return self.buy, self.sell

    @property
    def done(self) -> bool:
        return self.buy.done and self.sell.done

    def leg(self, side: Side) -> Leg:
        return self.buy if side == "buy" else self.sell

    def fail(self, side: Side, error: str) -> DoubleState:
        """La pierna no llegó al venue (error al enviar)."""
        return self._with(side, replace(self.leg(side), status="rejected", error=error))

    def uncertain(self, side: Side, error: str) -> DoubleState:
        """No se sabe si la pierna llegó al venue: se reconcilia (RNF-12)."""
        return self._with(side, replace(self.leg(side), status="unknown", error=error))

    def skip(self, side: Side) -> DoubleState:
        """La pierna que esperaba no se envía (D-75): cerrada con 0 lleno."""
        return self._with(side, replace(self.leg(side), status="skipped"))

    def resize(self, side: Side, qty: Decimal) -> DoubleState:
        """La pierna que esperaba sale por `qty`, lo que llenó la primera (D-75)."""
        leg = self.leg(side)
        return self._with(side, replace(leg, req=replace(leg.req, qty=qty), status="pending"))

    def _with(self, side: Side, leg: Leg) -> DoubleState:
        return replace(self, buy=leg) if side == "buy" else replace(self, sell=leg)


def start(
    double_id: str, buy: OrderRequest, sell: OrderRequest, now: int, *, held: Side | None = None
) -> DoubleState:
    """`held`: el lado que espera a la primera pierna (D-75)."""
    state = DoubleState(double_id, Leg(buy), Leg(sell), now)
    if held is not None:
        state = state._with(held, replace(state.leg(held), status="held"))
    return state


def on_order(state: DoubleState, order: Order, now: int) -> DoubleState:
    """Aplica una orden si es de una de las piernas; si no, devuelve el mismo estado."""
    for leg in state.legs:
        if leg.matches(order):
            updated = replace(
                leg, order_id=order.order_id, status=order.status, filled=order.filled, error=None
            )
            return _rebalance(state._with(leg.req.side, updated), now)
    return state


def _rebalance(state: DoubleState, now: int) -> DoubleState:
    if state.buy.filled == state.sell.filled:
        return replace(state, imbalance_since=None)
    if state.imbalance_since is None:
        return replace(state, imbalance_since=now)
    return state


def hanging(state: DoubleState, now: int, after_ms: int) -> str | None:
    """Motivo si hay pierna colgada ahora mismo; None si no."""
    buy, sell = state.legs
    if buy.filled == sell.filled:
        return None
    behind = buy if buy.filled < sell.filled else sell
    ahead = sell if behind is buy else buy
    gap = ahead.filled - behind.filled
    if behind.status in ENDED:
        return f"{behind.label} {ENDED[behind.status]} con {gap} {BASE_LABEL} sin cubrir"
    since = state.imbalance_since
    if since is not None and now - since > after_ms:
        return f"{behind.label} lleva más de {after_ms // 1000} s sin cubrir {gap} {BASE_LABEL}"
    return None
