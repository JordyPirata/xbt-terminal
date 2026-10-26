"""Propuestas y resultados de órdenes: lo que el daemon calcula y el cliente
enseña (diálogo de confirmación, mensajes). Datos puros, sin I/O: viajan por
el socket (D-66) y la UI los usa sin importar los servicios.

Spec: RF-06, RF-07, RF-08, RF-31 · design.md §6.2-6.4, §6.13 · Tareas: T-16,
T-18, T-19, T-65 · D-66
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .cross import sats
from .double import DoubleState
from .models import MarketSpec, Order, OrderRequest, Side, VenueId, asset_label
from .risk import Violation

NAMES: dict[VenueId, str] = {"nk": "NonKYC", "nx": "Neoxa"}


def _decimals(step: Decimal) -> int:
    return max(0, -int(step.as_tuple().exponent))


@dataclass(frozen=True, slots=True)
class Pending:
    """Orden validada, a falta de que el operador la confirme."""

    req: OrderRequest
    spec: MarketSpec
    notional: Decimal
    fee: Decimal  # taker como cota superior: si la orden queda en el book, paga maker
    basis: str | None = None  # de dónde sale la cantidad si se pidió en % (RF-17)

    @property
    def summary(self) -> str:
        qd, pd = _decimals(self.spec.lot_size), _decimals(self.spec.tick_size)
        r = self.req
        text = (
            f"{r.side.upper()} {r.qty:.{qd}f} {asset_label(self.spec.base)} @ {r.price:.{pd}f} "
            f"en {NAMES[r.venue]} = {self.notional:.8f} {asset_label(self.spec.quote)} "
            f"(fee ≤ {self.fee:.8f})"
        )
        return f"{text}\n{self.basis}" if self.basis else text


@dataclass(frozen=True, slots=True)
class Rejected:
    req: OrderRequest
    violations: tuple[Violation, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(v.code for v in self.violations)

    @property
    def message(self) -> str:
        return "rechazada: " + "; ".join(v.message for v in self.violations)


@dataclass(frozen=True, slots=True)
class Sent:
    order: Order


SIDE_LABEL: dict[Side, str] = {"buy": "compra", "sell": "venta"}


@dataclass(frozen=True, slots=True)
class PendingDouble:
    """Orden doble validada, a falta de la confirmación del operador."""

    id: str
    buy: Pending
    sell: Pending

    @property
    def gross(self) -> Decimal:
        return self.sell.notional - self.buy.notional

    @property
    def net(self) -> Decimal:
        """Con fee taker en las dos piernas: la cota pesimista."""
        return self.sell.notional * (1 - self.sell.spec.taker_fee) - self.buy.notional * (
            1 + self.buy.spec.taker_fee
        )

    @property
    def summary(self) -> str:
        def leg(p: Pending, label: str) -> str:
            qd, pd = _decimals(p.spec.lot_size), _decimals(p.spec.tick_size)
            return (
                f"{label} {p.req.qty:.{qd}f} {asset_label(p.spec.base)} @ {p.req.price:.{pd}f} "
                f"en {NAMES[p.req.venue]} = {p.notional:.8f} {asset_label(p.spec.quote)}"
            )

        return (
            f"ORDEN DOBLE {self.id}\n"
            f"{leg(self.buy, 'COMPRA')}\n{leg(self.sell, 'VENTA')}\n"
            f"spread bruto {self.gross:+.8f} BTC ({sats(self.gross):+.0f} sats) · "
            f"neto {self.net:+.8f} BTC con fee taker ({sats(self.net):+.0f} sats)"
        )


@dataclass(frozen=True, slots=True)
class DoubleRejected:
    double_id: str
    rejected: tuple[Rejected, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(c for r in self.rejected for c in r.codes)

    @property
    def message(self) -> str:
        parts = [
            f"{SIDE_LABEL[r.req.side]} en {NAMES[r.req.venue]}: "
            + "; ".join(v.message for v in r.violations)
            for r in self.rejected
        ]
        return "orden doble rechazada, no se envía ninguna pierna: " + " | ".join(parts)


@dataclass(frozen=True, slots=True)
class DoubleSent:
    state: DoubleState


@dataclass(frozen=True, slots=True)
class CancelPlan:
    """Lo que se va a cancelar, para el diálogo de confirmación."""

    scope: str  # order | venue | all
    orders: dict[VenueId, tuple[Order, ...]]

    @property
    def summary(self) -> str:
        n = sum(len(v) for v in self.orders.values())
        if self.scope == "order":
            ((venue, (order,)),) = self.orders.items()
            return (
                f"cancelar 1 orden en {NAMES[venue]}: {order.order_id} "
                f"{order.side} {order.qty} @ {order.price}"
            )
        what = f"{n} {'orden' if n == 1 else 'órdenes'}"
        if self.scope == "venue":
            ((venue, _),) = self.orders.items()
            what = f"{what} en {NAMES[venue]}"
        else:
            per = ", ".join(f"{len(v)} en {NAMES[vid]}" for vid, v in self.orders.items())
            what = f"{what} ({per})"
        return (
            f"cancelar {what}: todas las abiertas del par, "
            "también las que no se enviaron desde la terminal"
        )


@dataclass(frozen=True, slots=True)
class CancelRefused:
    message: str


@dataclass(frozen=True, slots=True)
class CancelResult:
    remaining: dict[VenueId, int]  # de las que se querían cancelar, las que siguen abiertas
    errors: dict[VenueId, str]

    @property
    def ok(self) -> bool:
        return not self.errors and not any(self.remaining.values())

    @property
    def message(self) -> str:
        parts = []
        for vid, n in self.remaining.items():
            if vid in self.errors:
                parts.append(f"{NAMES[vid]}: error: {self.errors[vid]}")
            elif n == 1:
                parts.append(f"{NAMES[vid]}: queda 1 abierta")
            elif n:
                parts.append(f"{NAMES[vid]}: quedan {n} abiertas")
        if not parts:
            return "cancelado · quedan 0 abiertas de las que se querían cancelar"
        return "cancelar: " + " · ".join(parts)


@dataclass(frozen=True, slots=True)
class Failed:
    req: OrderRequest
    error: str
    local: bool = False  # no salió de la terminal (el journal falló), no es del venue


@dataclass(frozen=True, slots=True)
class Uncertain:
    """No se sabe si la orden llegó al venue: se reconcilia (RNF-12)."""

    req: OrderRequest
    error: str
