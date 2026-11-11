"""TUI command line parser.

Only turns text into commands: tick rounding and limits belong to RiskCheck (T-06),
which knows the market. Rejected here is what makes no sense on any market: unknown
venue, malformed numbers, quantities ≤ 0.

Grammar (design.md §9):

    buy|sell <venue> <qty|N%> <price>      (N%: of the venue balance, RF-17)
    dbl <qty> <buy_venue> <buy_price> <sell_venue> <sell_price>
    orders [venue]
    cancel <order_id> | cancel <venue> | cancel all
    tf <1m|5m|1h|4h|1d>
    results [dia|7d|todo]                  (RF-25)
    deposit                                (RF-28)
    rebalance                              (RF-29)
    help

Spec: RF-03, RF-06, RF-07, RF-08, RF-15, RF-17, RF-25, RF-28, RF-29 · design.md §9
Tasks: T-05, T-21, T-23, T-40, T-42, T-45
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast, get_args

from .models import Side, Timeframe, VenueId
from .results import RANGES, Range

VENUES: tuple[str, ...] = get_args(VenueId)
TIMEFRAMES: tuple[str, ...] = get_args(Timeframe)

# Plain decimal notation only: "1e-2" or "NaN" are valid for Decimal, but nobody
# means to type them in an order.
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
    """Invalid command; the message is fit for the status bar."""


@dataclass(frozen=True, slots=True)
class Percent:
    """Quantity as a percentage of the venue balance (RF-17); resolved when preparing."""

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


BotAction = Literal["on", "off", "status"]
BOT_ACTIONS: tuple[str, ...] = get_args(BotAction)


@dataclass(frozen=True, slots=True)
class BotCommand:
    """Arm, stop or query the phase 2 bot (RF-18, RF-23)."""

    action: BotAction


@dataclass(frozen=True, slots=True)
class ShowResults:
    """Results window of the doubles (RF-25)."""

    range: Range = "dia"


@dataclass(frozen=True, slots=True)
class ShowDeposits:
    """Deposit addresses window (RF-28)."""


@dataclass(frozen=True, slots=True)
class ShowRebalance:
    """Rebalance plan window (RF-29)."""


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
    return cast(VenueId, venue)


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


def _parse_order(side: Side, args: list[str]) -> Command:
    _arity(side, args, 3)
    return PlaceOrder(
        venue=_venue(args[0]),
        side=side,
        qty=_quantity(args[1]),
        price=_positive(args[2], "precio"),
    )


def _parse_double(args: list[str]) -> Command:
    _arity("dbl", args, 5)
    quantity = _positive(args[0], "cantidad")
    buy_venue, sell_venue = _venue(args[1]), _venue(args[3])
    if buy_venue == sell_venue:
        raise CommandError("la orden doble necesita venues distintos en cada pierna")
    return DoubleOrder(
        qty=quantity,
        buy_venue=buy_venue,
        buy_price=_positive(args[2], "precio de compra"),
        sell_venue=sell_venue,
        sell_price=_positive(args[4], "precio de venta"),
    )


def _parse_orders(args: list[str]) -> Command:
    _arity("orders", args, 0, 1)
    return ListOrders(_venue(args[0]) if args else None)


def _parse_cancel(args: list[str]) -> Command:
    _arity("cancel", args, 1)
    target = args[0]
    if target.lower() == "all":
        return CancelAll()
    if target.lower() in VENUES:
        return CancelVenue(_venue(target))
    return CancelOrder(target)  # order ids are case sensitive


def _parse_timeframe(args: list[str]) -> Command:
    _arity("tf", args, 1)
    timeframe = args[0].lower()
    if timeframe not in TIMEFRAMES:
        raise CommandError(f"timeframe {args[0]!r}: usar {', '.join(TIMEFRAMES)}")
    return SetTimeframe(cast(Timeframe, timeframe))


def _parse_bot(args: list[str]) -> Command:
    _arity("bot", args, 1)
    action = args[0].lower()
    if action not in BOT_ACTIONS:
        raise CommandError(f"uso: {USAGE['bot']}")
    return BotCommand(cast(BotAction, action))


def _parse_results(args: list[str]) -> Command:
    _arity("results", args, 0, 1)
    rng = args[0].lower() if args else "dia"
    if rng not in RANGES:
        raise CommandError(f"uso: {USAGE['results']}")
    return ShowResults(cast(Range, rng))


def _no_args(name: str, command: Command) -> Callable[[list[str]], Command]:
    """Command taking no arguments; `name` is the USAGE entry."""

    def parse_it(args: list[str]) -> Command:
        _arity(name, args, 0)
        return command

    return parse_it


_PARSERS: dict[str, Callable[[list[str]], Command]] = {
    "buy": lambda args: _parse_order("buy", args),
    "sell": lambda args: _parse_order("sell", args),
    "dbl": _parse_double,
    "orders": _parse_orders,
    "cancel": _parse_cancel,
    "tf": _parse_timeframe,
    "bot": _parse_bot,
    "results": _parse_results,
    "deposit": _no_args("deposit", ShowDeposits()),
    "rebalance": _no_args("rebalance", ShowRebalance()),
    "help": _no_args("help", ShowHelp()),
    "?": _no_args("help", ShowHelp()),
}


def parse(text: str) -> Command:
    """Turn a TUI line into a command; `CommandError` if it makes no sense."""
    tokens = text.split()
    if not tokens:
        raise CommandError("comando vacío")
    parser = _PARSERS.get(tokens[0].lower())
    if parser is None:
        raise CommandError(f"comando desconocido {tokens[0]!r}: {', '.join(USAGE)}")
    return parser(tokens[1:])
