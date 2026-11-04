"""Double order: state of both legs and hanging leg (RF-07, PA-08, D-15).

Pure: takes orders and the time, and returns new states. A leg is identified first
by its `client_id` (the send response), then by `(venue, order_id)`, because Neoxa
does not keep the client_id.

Hanging leg (PA-08 assumption, generalized in D-15): filled quantities differ and
the leg behind is rejected or cancelled, or the imbalance lasts more than
`hanging_leg_after_s`. The imbalance clock starts when quantities stop matching
and resets if they match again. Nothing automatic happens: it only warns.

A leg whose send was uncertain (RNF-12) is `unknown` until reconciled: if it shows
up, it goes on like any other; if it does not exist, `rejected`.

In a bot double with `lead_venue` (D-75), the other leg is `held` (prepared, not
sent) until the first answers: it goes out for what that filled (`resize`) or
becomes `skipped`, which counts as closed with 0 filled.

Spec: RF-07, RF-20, CA-03, RNF-12 · design.md §6.4, §6.9 · Tasks: T-18, T-25, T-77
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from .models import BASE_LABEL, ZERO, Order, OrderRequest, OrderStatus, Side

# unknown: uncertain send (RNF-12); held and skipped: the bot's other leg (D-75)
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
        """The leg never reached the venue (send error)."""
        return self._with(side, replace(self.leg(side), status="rejected", error=error))

    def uncertain(self, side: Side, error: str) -> DoubleState:
        """Unknown whether the leg reached the venue: to be reconciled (RNF-12)."""
        return self._with(side, replace(self.leg(side), status="unknown", error=error))

    def skip(self, side: Side) -> DoubleState:
        """The waiting leg is not sent (D-75): closed with 0 filled."""
        return self._with(side, replace(self.leg(side), status="skipped"))

    def resize(self, side: Side, qty: Decimal) -> DoubleState:
        """The waiting leg goes out for `qty`, what the first filled (D-75)."""
        leg = self.leg(side)
        return self._with(side, replace(leg, req=replace(leg.req, qty=qty), status="pending"))

    def _with(self, side: Side, leg: Leg) -> DoubleState:
        return replace(self, buy=leg) if side == "buy" else replace(self, sell=leg)


def start(
    double_id: str, buy: OrderRequest, sell: OrderRequest, now: int, *, held: Side | None = None
) -> DoubleState:
    """`held`: the side waiting for the first leg (D-75)."""
    state = DoubleState(double_id, Leg(buy), Leg(sell), now)
    if held is not None:
        state = state._with(held, replace(state.leg(held), status="held"))
    return state


def on_order(state: DoubleState, order: Order, now: int) -> DoubleState:
    """Apply an order if it belongs to a leg; otherwise return the same state."""
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
    """Reason if a leg hangs right now; None otherwise."""
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
