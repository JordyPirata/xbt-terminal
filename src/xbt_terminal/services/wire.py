"""Models ↔ JSON for the daemon socket: one line, one object.

`encode` walks dataclasses, dicts and sequences and leaves `Decimal`s as text
(RNF-07: no `float` in money). `decode` rebuilds the requested type from its
annotations; a number where a `Decimal` goes is an error, not rounded.

Spec: RF-31, RNF-07 · design.md §6.13 · Tasks: T-52, T-62 · D-61, D-66
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from collections import abc, deque
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Any

from ..core.bot import BotLimits, BotState
from ..core.double import DoubleState
from ..core.inventory import InventoryTarget
from ..core.models import (
    BookSnapshot,
    Candle,
    ConnState,
    MarketSpec,
    OrderRequest,
    Timeframe,
    Trade,
    VenueId,
    WithdrawalFee,
)
from .account import VenueAccount
from .deposits import DepositRow

if typing.TYPE_CHECKING:
    from .engine import Engine
    from .market import VenueMarket

VENUES: tuple[VenueId, ...] = ("nk", "nx")


def encode(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: encode(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): encode(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | deque | set | frozenset):
        return [encode(v) for v in obj]
    return obj


def dumps(message: dict[str, Any]) -> bytes:
    return json.dumps(encode(message), ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def loads(line: bytes) -> dict[str, Any]:
    try:
        message = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"línea que no es JSON: {error}") from None
    if not isinstance(message, dict):
        raise ValueError("un mensaje es un objeto JSON")
    return message


@cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def decode(tp: Any, data: Any) -> Any:
    """Rebuild `tp` from the JSON `data`; `TypeError` or `ValueError` if it does not fit."""
    return _decoder(tp)(tp, data)


@cache
def _decoder(tp: Any) -> Callable[[Any, Any], Any]:
    """The decoder of `tp`, chosen once per type: it runs on every message."""
    origin = typing.get_origin(tp)
    for matches, decoder in _DECODERS:
        if matches(tp, origin):
            return decoder
    return _decode_unknown


def _decode_unknown(tp: Any, data: Any) -> Any:
    raise TypeError(f"tipo sin decodificar: {tp!r}")


def _decode_none(tp: Any, data: Any) -> None:
    if data is not None:
        raise TypeError(f"se esperaba null y llegó {data!r}")


def _decode_union(tp: Any, data: Any) -> Any:
    """Try each union type in order; the first that fits wins."""
    args = typing.get_args(tp)
    if data is None and type(None) in args:
        return None
    errors = []
    for arg in args:
        if arg is type(None):
            continue
        try:
            return decode(arg, data)
        except (TypeError, ValueError) as error:
            errors.append(str(error))
    raise TypeError(f"{data!r} no encaja en {tp}: {'; '.join(errors)}")


def _decode_literal(tp: Any, data: Any) -> Any:
    args = typing.get_args(tp)
    if data not in args:
        raise ValueError(f"{data!r} no es uno de {args}")
    return data


def _decode_decimal(tp: Any, data: Any) -> Decimal:
    if not isinstance(data, str):
        raise TypeError(f"un Decimal llega como texto, no {type(data).__name__}")
    try:
        return Decimal(data)
    except InvalidOperation:
        raise ValueError(f"Decimal inválido: {data!r}") from None


def _decode_dataclass(tp: Any, data: Any) -> Any:
    """Only the fields that arrive and go to `__init__`; the rest by default."""
    if not isinstance(data, dict):
        raise TypeError(f"{tp.__name__} llega como objeto")
    hints = _hints(tp)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(tp):
        if not f.init or f.name not in data:
            continue
        value = decode(hints[f.name], data[f.name])
        if isinstance(value, deque) and f.default_factory is not dataclasses.MISSING:
            fresh = f.default_factory()  # keeps the field's `maxlen`
            fresh.extend(value)
            value = fresh
        kwargs[f.name] = value
    return tp(**kwargs)


def _decode_mapping(tp: Any, data: Any) -> dict[Any, Any]:
    if not isinstance(data, dict):
        raise TypeError("se esperaba un objeto")
    key_t, val_t = typing.get_args(tp)
    return {decode(key_t, k): decode(val_t, v) for k, v in data.items()}


def _decode_sequence(tp: Any, data: Any) -> Any:
    """Lists, tuples (fixed length or `tuple[X, ...]`), deques and sets."""
    if not isinstance(data, list):
        raise TypeError("se esperaba una lista")
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
        return tuple(decode(a, v) for a, v in zip(args, data, strict=True))
    return origin(decode(args[0], v) for v in data)


def _decode_scalar(tp: Any, data: Any) -> Any:
    """int, str, bool and float; a bool is no number."""
    is_bool = isinstance(data, bool)
    if tp is float and isinstance(data, int) and not is_bool:
        return float(data)
    if not isinstance(data, tp) or (tp is int and is_bool):
        raise TypeError(f"se esperaba {tp.__name__} y llegó {data!r}")
    return data


# In order: the first that fits decides (`decode`).
_DECODERS: tuple[tuple[Callable[[Any, Any], bool], Callable[[Any, Any], Any]], ...] = (
    (lambda tp, origin: tp is Any, lambda tp, data: data),
    (lambda tp, origin: tp is type(None), _decode_none),
    (lambda tp, origin: origin in (typing.Union, types.UnionType), _decode_union),
    (lambda tp, origin: origin is typing.Literal, _decode_literal),
    (lambda tp, origin: tp is Decimal, _decode_decimal),
    (lambda tp, origin: dataclasses.is_dataclass(tp), _decode_dataclass),
    (lambda tp, origin: origin in (dict, typing.Mapping, abc.Mapping), _decode_mapping),
    (lambda tp, origin: origin in (list, tuple, deque, set, frozenset), _decode_sequence),
    (lambda tp, origin: tp in (int, str, bool, float), _decode_scalar),
)


# -- daemon messages ----------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ClientView:
    """What the screen needs from the daemon's config; goes in `hello`, so the client
    neither reads `config.toml` nor sees keys (D-66)."""

    book_levels: int
    timeframe: Timeframe
    fee_discounts: dict[VenueId, Decimal]  # for the cross panel only (RF-24)
    inventory: InventoryTarget | None  # RF-27
    bot_limits: BotLimits | None  # for the doubles that pay the rebalance (RF-29)


@dataclasses.dataclass(frozen=True, slots=True)
class BotView:
    state: BotState
    status_text: str
    summary: str  # parameters, for the `bot on` confirmation (RF-18)
    refusal: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class Snapshot:
    """What the client needs of the engine, except the market (`MarketView`)."""

    mode: str
    bot: BotView | None
    accounts: dict[VenueId, VenueAccount]
    doubles: dict[str, DoubleState]
    hanging: dict[str, str]
    uncertain: list[OrderRequest]
    in_flight: int
    faults: int
    can_trade: dict[VenueId, bool]
    # venues with keys for deposits and withdrawal fees, and whether there are results (D-66)
    deposit_venues: tuple[VenueId, ...] = ()
    fee_venues: tuple[VenueId, ...] = ()
    has_results: bool = False


def snapshot(engine: Engine) -> Snapshot:
    bot = engine.bot
    orders = engine.orders
    return Snapshot(
        mode=engine.mode,
        bot=(
            BotView(bot.state, bot.status_text(), bot.summary, bot.refusal())
            if bot is not None
            else None
        ),
        accounts=dict(engine.account.state) if engine.account is not None else {},
        doubles=dict(orders.doubles) if orders is not None else {},
        hanging=dict(orders.hanging) if orders is not None else {},
        uncertain=[u.req for u in orders.uncertain.values()] if orders is not None else [],
        in_flight=orders.in_flight if orders is not None else 0,
        faults=engine.fault_count,
        can_trade=(
            {venue_id: orders.can_trade(venue_id) for venue_id in VENUES}
            if orders is not None
            else {}
        ),
        deposit_venues=engine.deposits.venues if engine.deposits is not None else (),
        fee_venues=engine.rebalance.venues if engine.rebalance is not None else (),
        has_results=engine.results is not None,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class MarketView:
    """Market snapshot of a venue for the client: on connecting and each time the venue
    reloads (D-66). Then book, trades and connection arrive."""

    spec: MarketSpec | None
    book: BookSnapshot | None  # None: invalid book
    candles: dict[Timeframe, tuple[Candle, ...]]
    last_trade: Trade | None
    conn: ConnState | None
    ready: bool
    error: str | None


def market_view(state: VenueMarket, depth: int) -> MarketView:
    return MarketView(
        spec=state.spec,
        book=state.book.snapshot(depth),
        candles={tf: builder.candles for tf, builder in state.candles.items()},
        last_trade=state.last_trade,
        conn=state.conn,
        ready=state.ready,
        error=state.error,
    )


# -- replies that are not a single dataclass (D-66) --------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Rows:
    """Rows of deposit addresses (RF-28)."""

    rows: tuple[DepositRow, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class FeeRow:
    venue: VenueId
    asset: str
    fee: WithdrawalFee | str  # str: why it did not arrive (RF-29)


@dataclasses.dataclass(frozen=True, slots=True)
class Fees:
    """Withdrawal fees by venue and asset; a JSON key cannot be a tuple, so they travel
    as rows."""

    fees: tuple[FeeRow, ...]

