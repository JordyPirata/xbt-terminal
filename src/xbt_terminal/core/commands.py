"""Parser de la línea de comandos de la TUI.

Solo traduce texto a comandos: no cuantiza al tick ni valida límites, eso es de
RiskCheck (T-06), que conoce el mercado. Aquí se rechaza lo que no tiene sentido
en ningún mercado: venue desconocido, números mal escritos, cantidades ≤ 0.

Gramática (design.md §9):

    buy|sell <venue> <qty|N%> <price>      (N%: del saldo del venue, RF-17)
    dbl <qty> <venue_compra> <precio_compra> <venue_venta> <precio_venta>
    orders [venue]
    cancel <order_id> | cancel <venue> | cancel all
    tf <1m|5m|1h|4h|1d>
    results [dia|7d|todo]                  (RF-25)
    deposit                                (RF-28)
    rebalance                              (RF-29)
    help

Spec: RF-03, RF-06, RF-07, RF-08, RF-15, RF-17, RF-25, RF-28, RF-29 · design.md §9
Tareas: T-05, T-21, T-23, T-40, T-42, T-45
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, get_args

from .models import Side, Timeframe, VenueId
from .results import RANGES, Range

VENUES: tuple[str, ...] = get_args(VenueId)
TIMEFRAMES: tuple[str, ...] = get_args(Timeframe)

# Solo notación decimal simple: "1e-2" o "NaN" son válidos para Decimal pero
# no son algo que alguien quiera teclear en una orden.
_NUMBER = re.compile(r"^(\d+(\.\d*)?|\.\d+)$")

USAGE = {
    "buy": "buy <venue> <cantidad|N%> <precio>",
    "sell": "sell <venue> <cantidad|N%> <precio>",
    "dbl": "dbl <cantidad> <venue_compra> <precio_compra> <venue_venta> <precio_venta>",
    "orders": "orders [venue]",
    "cancel": "cancel <order_id> | cancel <venue> | cancel all",
    "tf": "tf <1m|5m|1h|4h|1d>",
    "bot": "bot on|off|status",
    "results": "results [dia|7d|todo]",
    "deposit": "deposit",
    "rebalance": "rebalance",
    "help": "help",
}


class CommandError(ValueError):
    """Comando inválido; el mensaje es apto para mostrarlo en la barra de estado."""


@dataclass(frozen=True, slots=True)
class Percent:
    """Cantidad como porcentaje del saldo del venue (RF-17); se resuelve al preparar."""

    value: Decimal


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    venue: VenueId
    side: Side
    qty: Decimal | Percent
    price: Decimal


@dataclass(frozen=True, slots=True)
class DoubleOrder:
    qty: Decimal
    buy_venue: VenueId
    buy_price: Decimal
    sell_venue: VenueId
    sell_price: Decimal


@dataclass(frozen=True, slots=True)
class ListOrders:
    venue: VenueId | None


@dataclass(frozen=True, slots=True)
class CancelOrder:
    order_id: str


@dataclass(frozen=True, slots=True)
class CancelVenue:
    venue: VenueId


@dataclass(frozen=True, slots=True)
class CancelAll:
    pass


@dataclass(frozen=True, slots=True)
class SetTimeframe:
    timeframe: Timeframe


@dataclass(frozen=True, slots=True)
class ShowHelp:
    pass


@dataclass(frozen=True, slots=True)
class BotCommand:
    """Armar, parar o consultar el bot de fase 2 (RF-18, RF-23)."""

    action: Literal["on", "off", "status"]


@dataclass(frozen=True, slots=True)
class ShowResults:
    """Ventana de resultados de las dobles (RF-25)."""

    range: Range = "dia"


@dataclass(frozen=True, slots=True)
class ShowDeposits:
    """Ventana de direcciones de depósito (RF-28)."""


@dataclass(frozen=True, slots=True)
class ShowRebalance:
    """Ventana del plan de reequilibrio (RF-29)."""


Command = (
    PlaceOrder
    | DoubleOrder
    | ListOrders
    | CancelOrder
    | CancelVenue
    | CancelAll
    | SetTimeframe
    | ShowHelp
    | BotCommand
    | ShowResults
    | ShowDeposits
    | ShowRebalance
)


def _venue(token: str) -> VenueId:
    venue = token.lower()
    if venue not in VENUES:
        raise CommandError(f"venue desconocido {token!r}: usar {' o '.join(VENUES)}")
    return venue  # type: ignore[return-value]


def _positive(token: str, what: str) -> Decimal:
    if "," in token:
        raise CommandError(f"{what} {token!r}: usar punto decimal, no coma")
    if not _NUMBER.match(token):
        raise CommandError(f"{what} {token!r} no es un número")
    value = Decimal(token)
    if value <= 0:
        raise CommandError(f"{what} debe ser mayor que 0")
    return value


def _quantity(token: str) -> Decimal | Percent:
    if not token.endswith("%"):
        return _positive(token, "cantidad")
    value = _positive(token[:-1], "porcentaje") if token[:-1] else None
    if value is None:
        raise CommandError("porcentaje vacío: escribir p. ej. 25%")
    if value > 100:
        raise CommandError(f"porcentaje {token!r}: como mucho 100%")
    return Percent(value)


def _arity(name: str, args: list[str], *counts: int) -> None:
    if len(args) not in counts:
        raise CommandError(f"uso: {USAGE[name]}")


def parse(text: str) -> Command:
    tokens = text.split()
    if not tokens:
        raise CommandError("comando vacío")
    name, args = tokens[0].lower(), tokens[1:]

    if name in ("buy", "sell"):
        _arity(name, args, 3)
        return PlaceOrder(
            venue=_venue(args[0]),
            side=name,  # type: ignore[arg-type]
            qty=_quantity(args[1]),
            price=_positive(args[2], "precio"),
        )

    if name == "dbl":
        _arity(name, args, 5)
        qty = _positive(args[0], "cantidad")
        buy_venue, sell_venue = _venue(args[1]), _venue(args[3])
        if buy_venue == sell_venue:
            raise CommandError("la orden doble necesita venues distintos en cada pierna")
        return DoubleOrder(
            qty=qty,
            buy_venue=buy_venue,
            buy_price=_positive(args[2], "precio de compra"),
            sell_venue=sell_venue,
            sell_price=_positive(args[4], "precio de venta"),
        )

    if name == "orders":
        _arity(name, args, 0, 1)
        return ListOrders(_venue(args[0]) if args else None)

    if name == "cancel":
        _arity(name, args, 1)
        target = args[0]
        if target.lower() == "all":
            return CancelAll()
        if target.lower() in VENUES:
            return CancelVenue(_venue(target))
        return CancelOrder(target)  # los ids de orden distinguen mayúsculas

    if name == "tf":
        _arity(name, args, 1)
        if args[0].lower() not in TIMEFRAMES:
            raise CommandError(f"timeframe {args[0]!r}: usar {', '.join(TIMEFRAMES)}")
        return SetTimeframe(args[0].lower())  # type: ignore[arg-type]

    if name == "bot":
        _arity(name, args, 1)
        action = args[0].lower()
        if action not in ("on", "off", "status"):
            raise CommandError(f"uso: {USAGE['bot']}")
        return BotCommand(action)  # type: ignore[arg-type]

    if name == "results":
        _arity(name, args, 0, 1)
        rng = args[0].lower() if args else "dia"
        if rng not in RANGES:
            raise CommandError(f"uso: {USAGE['results']}")
        return ShowResults(rng)  # type: ignore[arg-type]

    if name == "deposit":
        _arity(name, args, 0)
        return ShowDeposits()

    if name == "rebalance":
        _arity(name, args, 0)
        return ShowRebalance()

    if name in ("help", "?"):
        _arity("help", args, 0)
        return ShowHelp()

    raise CommandError(f"comando desconocido {tokens[0]!r}: {', '.join(USAGE)}")
