"""Modelos ↔ JSON para el socket del daemon: una línea, un objeto.

`encode` recorre dataclasses, diccionarios y secuencias y deja los `Decimal`
como texto (RNF-07: nada de `float` en dinero). `decode` rehace el tipo pedido
con sus anotaciones; un número donde va un `Decimal` es error, no se redondea.

Spec: RF-31, RNF-07 · design.md §6.13 · Tareas: T-52, T-62 · D-61, D-66
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from collections import abc, deque
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
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"línea que no es JSON: {exc}") from None
    if not isinstance(message, dict):
        raise ValueError("un mensaje es un objeto JSON")
    return message


@cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def decode(tp: Any, data: Any) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if tp is Any:
        return data
    if tp is type(None):
        if data is not None:
            raise TypeError(f"se esperaba null y llegó {data!r}")
        return None
    if origin in (typing.Union, types.UnionType):
        if data is None and type(None) in args:
            return None
        errors = []
        for arg in args:
            if arg is type(None):
                continue
            try:
                return decode(arg, data)
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
        raise TypeError(f"{data!r} no encaja en {tp}: {'; '.join(errors)}")
    if origin is typing.Literal:
        if data not in args:
            raise ValueError(f"{data!r} no es uno de {args}")
        return data
    if tp is Decimal:
        if not isinstance(data, str):
            raise TypeError(f"un Decimal llega como texto, no {type(data).__name__}")
        try:
            return Decimal(data)
        except InvalidOperation:
            raise ValueError(f"Decimal inválido: {data!r}") from None
    if dataclasses.is_dataclass(tp):
        if not isinstance(data, dict):
            raise TypeError(f"{tp.__name__} llega como objeto")
        hints = _hints(tp)
        kwargs: dict[str, Any] = {}
        for f in dataclasses.fields(tp):
            if not f.init or f.name not in data:
                continue
            value = decode(hints[f.name], data[f.name])
            if isinstance(value, deque) and f.default_factory is not dataclasses.MISSING:
                fresh = f.default_factory()  # conserva el `maxlen` del campo
                fresh.extend(value)
                value = fresh
            kwargs[f.name] = value
        return tp(**kwargs)
    if origin in (dict, typing.Mapping, abc.Mapping):
        if not isinstance(data, dict):
            raise TypeError("se esperaba un objeto")
        key_t, val_t = args
        return {decode(key_t, k): decode(val_t, v) for k, v in data.items()}
    if origin in (list, tuple, deque, set, frozenset):
        if not isinstance(data, list):
            raise TypeError("se esperaba una lista")
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            return tuple(decode(a, v) for a, v in zip(args, data, strict=True))
        items = [decode(args[0], v) for v in data]
        if origin is deque:
            return deque(items)
        return origin(items)
    if tp in (int, str, bool, float):
        if tp is float and isinstance(data, int) and not isinstance(data, bool):
            return float(data)
        if not isinstance(data, tp) or (tp is int and isinstance(data, bool)):
            raise TypeError(f"se esperaba {tp.__name__} y llegó {data!r}")
        return data
    raise TypeError(f"tipo sin decodificar: {tp!r}")


# -- mensajes del daemon ----------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ClientView:
    """Lo que la pantalla necesita de la config del daemon; va en `hello`, así
    el cliente no lee `config.toml` ni ve keys (D-66)."""

    book_levels: int
    timeframe: Timeframe
    fee_discounts: dict[VenueId, Decimal]  # solo para el panel cruzado (RF-24)
    inventory: InventoryTarget | None  # RF-27
    bot_limits: BotLimits | None  # para las dobles que pagan el reequilibrio (RF-29)


@dataclasses.dataclass(frozen=True, slots=True)
class BotView:
    state: BotState
    status_text: str
    summary: str  # parámetros, para la confirmación de `bot on` (RF-18)
    refusal: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class Snapshot:
    """Lo que el cliente necesita del motor, salvo el mercado (`MarketView`)."""

    mode: str
    bot: BotView | None
    accounts: dict[VenueId, VenueAccount]
    doubles: dict[str, DoubleState]
    hanging: dict[str, str]
    uncertain: list[OrderRequest]
    in_flight: int
    faults: int
    can_trade: dict[VenueId, bool]
    # venues con keys para depósitos y comisiones de retiro, y si hay resultados (D-66)
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
        faults=sum(f.count for f in engine.faults),
        can_trade={vid: orders.can_trade(vid) for vid in VENUES} if orders is not None else {},
        deposit_venues=engine.deposits.venues if engine.deposits is not None else (),
        fee_venues=engine.rebalance.venues if engine.rebalance is not None else (),
        has_results=engine.results is not None,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class MarketView:
    """Foto del mercado de un venue para el cliente: al conectar y cada vez que
    el venue se carga de nuevo (D-66). Después llegan book, trades y conexión."""

    spec: MarketSpec | None
    book: BookSnapshot | None  # None: book no válido
    candles: dict[Timeframe, tuple[Candle, ...]]
    last_trade: Trade | None
    conn: ConnState | None
    ready: bool
    error: str | None


def market_view(st: VenueMarket, depth: int) -> MarketView:
    return MarketView(
        spec=st.spec,
        book=st.book.snapshot(depth),
        candles={tf: builder.candles for tf, builder in st.candles.items()},
        last_trade=st.last_trade,
        conn=st.conn,
        ready=st.ready,
        error=st.error,
    )


# -- respuestas que no son un solo dataclass (D-66) --------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Rows:
    """Las filas de direcciones de depósito (RF-28)."""

    rows: tuple[DepositRow, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class FeeRow:
    venue: VenueId
    asset: str
    fee: WithdrawalFee | str  # str: por qué no llegó (RF-29)


@dataclasses.dataclass(frozen=True, slots=True)
class Fees:
    """Las comisiones de retiro por venue y activo; en JSON una clave no puede
    ser una tupla, así que viajan como filas."""

    fees: tuple[FeeRow, ...]

