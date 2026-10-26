"""Resultado realizado de cada orden doble, sacado del journal (RF-25).

Puro: recibe filas del journal ya leídas y devuelve un `DoubleResult` por doble,
un resumen y la curva del acumulado. No es un PnL de cartera: no valora
inventario ni lo llenado de más en una pierna (D-44).

    order_sent(client_id, double, side) ─┐
    order_ack(client_id, order_id)       ├─▶ fills por pierna ─▶ DoubleResult
    fill · fill_fee · bot_fire · ...  ───┘

Un fill se asigna a su orden por `client_id`, si no por `order_id` (vía
`order_ack`) y, si no trae ninguno (Neoxa, D-45), a la única orden del mismo
venue y lado enviada antes, con precio compatible con su límite y cantidad
pendiente suficiente; si hay varias, a la única enviada en el último minuto.
Si sigue habiendo dudas, queda sin asignar y se cuenta.

Resultado = casada × (medio venta - medio compra) - fees en BTC, con
casada = mín(llenado compra, llenado venta). Una fee en BTCB2 se valora al
precio de su fill; una en otro token se valora al precio que llegue en `prices`
(el token de fee del venue, D-65) y, sin precio, se apunta aparte sin restar.
Un `fill_fee` (el venue corrigió la fee, D-64) sustituye la del fill.

El `Report` lleva también la serie del valor de la cuenta (RF-32, D-69).

Spec: RF-11, RF-24, RF-25, RF-32, CA-11 · design.md §6.10 · D-44, D-45, D-64,
D-65, D-69 · Tareas: T-40, T-59, T-60, T-69
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .models import ZERO, Side, VenueId
from .valuation import ValueSample, series

DAY_MS = 86_400_000
RECENT_MS = 60_000  # una toma del bot se llena en segundos (D-45)
BASE = "BTCB2"
QUOTE = "BTC"

Origin = Literal["bot", "manual"]
Status = Literal["closed", "hanging", "no_fills"]
Range = Literal["dia", "7d", "todo"]
RANGES: tuple[Range, ...] = ("dia", "7d", "todo")
_FIELDS = ("ts", "mode", "event", "data")


def read_rows(lines: Iterable[str]) -> tuple[list[dict[str, Any]], int]:
    """Filas JSON del journal y cuántas no se pudieron leer (las vacías no cuentan)."""
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict) or any(k not in row for k in _FIELDS):
            bad += 1
            continue
        rows.append(row)
    return rows, bad


def range_start(rng: Range, now_ms: int) -> int | None:
    """Inicio del rango en ms: el día UTC de hoy, los 7 últimos días o todo (None)."""
    today = now_ms // DAY_MS * DAY_MS
    if rng == "dia":
        return today
    if rng == "7d":
        return today - 6 * DAY_MS
    return None


@dataclass(slots=True)
class _Order:
    venue: VenueId
    side: Side
    qty: Decimal
    price: Decimal
    sent_at: int
    double: str | None
    filled: Decimal = ZERO
    rejected: bool = False

    def takes(self, venue: str, side: str, qty: Decimal, price: Decimal, at: int) -> bool:
        if self.rejected or self.venue != venue or self.side != side or self.sent_at > at:
            return False
        if self.filled + qty > self.qty:
            return False
        return price <= self.price if side == "buy" else price >= self.price


@dataclass(slots=True)
class _Leg:
    venue: VenueId | None = None
    filled: Decimal = ZERO
    cost: Decimal = ZERO  # Σ qty × price, en BTC


@dataclass(slots=True)
class _Double:
    id: str
    started: int
    buy: _Leg = field(default_factory=_Leg)
    sell: _Leg = field(default_factory=_Leg)
    fees: Decimal = ZERO
    other_fees: dict[str, Decimal] = field(default_factory=dict)
    expected_pct: Decimal | None = None
    hanging: bool = False


@dataclass(frozen=True, slots=True)
class DoubleResult:
    id: str
    origin: Origin
    started: int  # ms epoch del primer envío
    buy_venue: VenueId | None
    sell_venue: VenueId | None
    buy_filled: Decimal
    sell_filled: Decimal
    buy_avg: Decimal | None
    sell_avg: Decimal | None
    matched: Decimal
    fees: Decimal  # BTC
    other_fees: tuple[tuple[str, Decimal], ...]
    mismatch: Decimal  # BTCB2: compra - venta
    status: Status
    result: Decimal | None  # BTC; solo en las cerradas
    result_pct: Decimal | None  # sobre lo pagado por la parte casada
    expected_pct: Decimal | None  # neta esperada del bot
    # BTC: las de `other_fees` con precio, que también restan del resultado (D-65)
    token_fees: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class Summary:
    closed: int
    total: Decimal
    mean: Decimal | None
    best: Decimal | None
    worst: Decimal | None
    hanging: int
    no_fills: int
    unassigned: int  # fills que no se pudieron asignar a una orden
    bad_rows: int
    # fees de las cerradas en otros activos sin precio (D-63): no restan del total
    other_fees: tuple[tuple[str, Decimal], ...] = ()
    # precios en BTC con que se valoraron las fees en token de las cerradas (D-65)
    prices: tuple[tuple[str, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class Report:
    doubles: tuple[DoubleResult, ...]
    summary: Summary
    curve: tuple[tuple[int, Decimal], ...]  # (inicio de la doble, acumulado)
    # valor de la cuenta en el rango (RF-32, D-69): muestras `value` del journal
    values: tuple[ValueSample, ...] = ()


def _part(leg: _Leg, qty: Decimal) -> Decimal:
    """Lo pagado o cobrado por `qty` de la pierna, a su precio medio."""
    return leg.cost if qty == leg.filled else qty * leg.cost / leg.filled


def _add_fee(d: _Double, fee: Decimal, asset: str | None, price: Decimal) -> None:
    """Suma (o resta, con fee negativa) la fee de un fill: en BTC, la de BTCB2 a su
    precio; la de otro activo, aparte y sin valorar."""
    if fee == 0 or asset in (None, QUOTE):
        d.fees += fee
    elif asset == BASE:
        d.fees += fee * price
    else:
        left = d.other_fees.get(asset, ZERO) + fee
        if left == 0:
            d.other_fees.pop(asset, None)
        else:
            d.other_fees[asset] = left


def _ms(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def _dec(value: Any) -> Decimal:
    out = Decimal(str(value))
    if not out.is_finite():
        raise ValueError(f"no es un número: {value!r}")
    return out


class _Builder:
    def __init__(self) -> None:
        self.orders: dict[str, _Order] = {}
        self.acks: dict[tuple[str, str], str] = {}
        self.doubles: dict[str, _Double] = {}
        self.fired: dict[str, Decimal | None] = {}
        self.hanging: set[str] = set()
        self.trades: set[tuple[str, str]] = set()
        # fee apuntada de cada fill asignado a una doble, para `fill_fee` (D-64)
        self.fill_fees: dict[tuple[str, str], tuple[_Double, Decimal, Decimal, str | None]] = {}
        self.unassigned = 0
        self.bad = 0

    def feed(self, row: Mapping[str, Any]) -> None:
        if row.get("mode") != "live":
            return
        event, data = row.get("event"), row.get("data") or {}
        handler = getattr(self, f"_on_{event}", None)
        if handler is None:
            return
        try:
            handler(row, data)
        except (ValueError, KeyError, TypeError, InvalidOperation, AttributeError):
            self.bad += 1  # una fila rara no impide calcular el resto

    def _on_order_sent(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        cid, at, double = row["client_id"], _ms(row["ts"]), data.get("double")
        if cid is None or data["side"] not in ("buy", "sell"):
            raise ValueError("order_sent sin client_id o con lado raro")
        order = _Order(
            row["venue"], data["side"], _dec(data["qty"]), _dec(data["price"]), at, double
        )
        self.orders[cid] = order
        if double is not None:
            d = self.doubles.setdefault(double, _Double(double, at))
            getattr(d, order.side).venue = order.venue

    def _on_order_ack(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        if row["client_id"] is not None and data.get("order_id"):
            self.acks[(row["venue"], str(data["order_id"]))] = row["client_id"]
        if data.get("status") == "rejected":
            self._on_order_error(row, data)

    def _on_order_error(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        order = self.orders.get(row["client_id"])
        if order is not None:
            order.rejected = True

    def _on_bot_fire(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        pct = data.get("net_pct")
        self.fired[data["double"]] = _dec(pct) if pct is not None else None

    def _on_hanging_leg(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        self.hanging.add(data["double"])

    def _on_fill(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        venue, side = row["venue"], data["side"]
        qty, price, at = _dec(data["qty"]), _dec(data["price"]), _ms(row["ts"])
        fee = _dec(data.get("fee") or 0)
        key = (venue, str(data["trade_id"]))
        if key in self.trades:
            return
        self.trades.add(key)
        cid = self._owner(row, data, venue, side, qty, price, at)
        order = self.orders.get(cid) if cid is not None else None
        if order is None:
            self.unassigned += 1
            return
        order.filled += qty
        if order.double is None:
            return
        d = self.doubles[order.double]
        leg = getattr(d, order.side)
        leg.filled += qty
        leg.cost += qty * price
        asset = data.get("fee_asset")
        _add_fee(d, fee, asset, price)
        self.fill_fees[key] = (d, price, fee, asset)

    def _on_fill_fee(self, row: Mapping[str, Any], data: Mapping[str, Any]) -> None:
        """El venue corrigió la fee de un fill (D-64): sustituye la apuntada. De un
        fill que no está en una doble, no hay nada que corregir."""
        key = (row["venue"], str(data["trade_id"]))
        known = self.fill_fees.get(key)
        if known is None:
            return
        d, price, old_fee, old_asset = known
        fee, asset = _dec(data.get("fee") or 0), data.get("fee_asset")
        _add_fee(d, -old_fee, old_asset, price)
        _add_fee(d, fee, asset, price)
        self.fill_fees[key] = (d, price, fee, asset)

    def _owner(
        self,
        row: Mapping[str, Any],
        data: Mapping[str, Any],
        venue: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        at: int,
    ) -> str | None:
        cid = row.get("client_id")
        if cid in self.orders:
            return cid
        oid = data.get("order_id")
        if oid and (venue, str(oid)) in self.acks:
            return self.acks[(venue, str(oid))]
        # sin ids (Neoxa, D-45): la única orden compatible, o la única reciente
        fits = [c for c, o in self.orders.items() if o.takes(venue, side, qty, price, at)]
        if len(fits) > 1:
            fits = [c for c in fits if at - self.orders[c].sent_at <= RECENT_MS]
        return fits[0] if len(fits) == 1 else None

    def result(self, d: _Double, prices: Mapping[str, Decimal]) -> DoubleResult:
        buy, sell = d.buy, d.sell
        token_fees = ZERO
        for asset, qty in d.other_fees.items():
            if asset in prices:
                token_fees += qty * prices[asset]
        buy_avg = buy.cost / buy.filled if buy.filled else None
        sell_avg = sell.cost / sell.filled if sell.filled else None
        matched = min(buy.filled, sell.filled)
        mismatch = buy.filled - sell.filled
        status: Status
        if buy_avg is None or sell_avg is None:
            status = "no_fills"
        elif d.id in self.hanging and mismatch != 0:
            status = "hanging"
        else:
            status = "closed"
        result = pct = None
        if status == "closed":
            # con la pierna casada entera, su coste exacto: el medio lleva una división
            paid, got = _part(buy, matched), _part(sell, matched)
            result = got - paid - d.fees - token_fees
            pct = result / paid * 100
        return DoubleResult(
            id=d.id,
            origin="bot" if d.id in self.fired else "manual",
            started=d.started,
            buy_venue=buy.venue,
            sell_venue=sell.venue,
            buy_filled=buy.filled,
            sell_filled=sell.filled,
            buy_avg=buy_avg,
            sell_avg=sell_avg,
            matched=matched,
            fees=d.fees,
            other_fees=tuple(sorted(d.other_fees.items())),
            mismatch=mismatch,
            status=status,
            result=result,
            result_pct=pct,
            expected_pct=self.fired.get(d.id),
            token_fees=token_fees,
        )


def compute(
    rows: Iterable[Mapping[str, Any]],
    *,
    since_ms: int | None = None,
    bad_lines: int = 0,
    prices: Mapping[str, Decimal] | None = None,
) -> Report:
    """Resultados de las dobles que empezaron desde `since_ms` (todas si None).

    Las filas se procesan en el orden del journal; conviene pasar también las
    de antes del rango, para que los fills se asignen igual que en el día entero.
    `prices`: BTC por unidad de los tokens de fee (D-65); sin precio, su fee va
    aparte y no resta."""
    prices = prices or {}
    rows = list(rows)
    b = _Builder()
    for row in rows:
        b.feed(row)
    values, bad_values = series(rows, since_ms)
    doubles = tuple(
        b.result(d, prices)
        for d in sorted(b.doubles.values(), key=lambda d: (d.started, d.id))
        if since_ms is None or d.started >= since_ms
    )
    closed = [d.result for d in doubles if d.result is not None]
    other: dict[str, Decimal] = {}
    used: dict[str, Decimal] = {}
    for d in doubles:
        if d.result is not None:
            for asset, qty in d.other_fees:
                if asset in prices:
                    used[asset] = prices[asset]
                else:
                    other[asset] = other.get(asset, ZERO) + qty
    curve: list[tuple[int, Decimal]] = []
    acc = ZERO
    for d in doubles:
        if d.result is not None:
            acc += d.result
            curve.append((d.started, acc))
    summary = Summary(
        closed=len(closed),
        total=sum(closed, ZERO),
        mean=sum(closed, ZERO) / len(closed) if closed else None,
        best=max(closed) if closed else None,
        worst=min(closed) if closed else None,
        hanging=sum(d.status == "hanging" for d in doubles),
        no_fills=sum(d.status == "no_fills" for d in doubles),
        unassigned=b.unassigned,
        bad_rows=b.bad + bad_lines + bad_values,
        other_fees=tuple(sorted(other.items())),
        prices=tuple(sorted(used.items())),
    )
    return Report(doubles, summary, tuple(curve), values)
