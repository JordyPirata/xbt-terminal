"""Valor de la cuenta en el tiempo (RF-32, D-69).

Puro: arma la muestra que el daemon apunta como evento `value` a partir del
inventario de RF-05, y lee del journal la serie de un rango con su resumen. No
es un PnL: depósitos y retiros salen como saltos, sin ajustar.

    inventory() ─▶ sample() ─▶ journal "value" ─▶ series() ─▶ summarize()

Spec: RF-05, RF-32, RNF-10, CA-17 · design.md §6.5 · D-69 · Tareas: T-69
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .inventory import Inventory
from .models import ZERO, VenueId

HUNDRED = Decimal(100)
# un salto mayor entre dos muestras seguidas es una transferencia, no el mercado (D-69)
JUMP_PCT = Decimal(5)


@dataclass(frozen=True, slots=True)
class VenueValue:
    venue: VenueId
    btc: Decimal
    btcb2: Decimal
    mid: Decimal
    value: Decimal  # BTC


@dataclass(frozen=True, slots=True)
class ValueSample:
    ts: int  # ms epoch del múltiplo del intervalo
    value: Decimal  # BTC, los dos venues
    every_ms: int  # intervalo con que se muestreó: decide qué es un hueco
    venues: tuple[VenueValue, ...]


@dataclass(frozen=True, slots=True)
class ValueSummary:
    since: int
    until: int
    first: Decimal
    last: Decimal
    change: Decimal
    change_pct: Decimal | None  # sobre la primera; None si era 0
    high: Decimal
    low: Decimal
    count: int


def sample(
    inv: Inventory, mids: Mapping[VenueId, Decimal | None], ts: int, every_ms: int
) -> ValueSample | None:
    """La muestra del inventario, o None si falta el saldo o el mid de algún
    venue: un total a medias engañaría (como RF-05)."""
    if inv.value is None:
        return None
    venues = []
    for vid, item in sorted(inv.venues.items()):
        mid = mids.get(vid)
        if item is None or item.value is None or mid is None:
            return None
        venues.append(VenueValue(vid, item.btc, item.btcb2, mid, item.value))
    return ValueSample(ts, inv.value, every_ms, tuple(venues))


def sample_data(s: ValueSample) -> dict[str, Any]:
    """El `data` del evento `value` del journal."""
    return {
        "at": s.ts,
        "every_ms": s.every_ms,
        "value": s.value,
        "venues": {
            v.venue: {"btc": v.btc, "btcb2": v.btcb2, "mid": v.mid, "value": v.value}
            for v in s.venues
        },
    }


def _dec(value: Any) -> Decimal:
    out = Decimal(str(value))
    if not out.is_finite():
        raise ValueError(f"no es un número: {value!r}")
    return out


def _ms(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def _parse(row: Mapping[str, Any]) -> ValueSample:
    data = row["data"]
    venues = tuple(
        VenueValue(vid, _dec(v["btc"]), _dec(v["btcb2"]), _dec(v["mid"]), _dec(v["value"]))
        for vid, v in sorted(data["venues"].items())
    )
    at = data.get("at")
    ts = at if isinstance(at, int) and not isinstance(at, bool) else _ms(row["ts"])
    return ValueSample(ts, _dec(data["value"]), int(data["every_ms"]), venues)


def series(
    rows: Iterable[Mapping[str, Any]], since_ms: int | None
) -> tuple[tuple[ValueSample, ...], int]:
    """Las muestras desde `since_ms` (todas si None), por hora, y cuántas filas
    `value` no se pudieron leer. El modo no importa: los saldos son reales también
    en dry-run (D-69)."""
    out: list[ValueSample] = []
    bad = 0
    for row in rows:
        if row.get("event") != "value":
            continue
        try:
            s = _parse(row)
        except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
            bad += 1
            continue
        if since_ms is None or s.ts >= since_ms:
            out.append(s)
    out.sort(key=lambda s: s.ts)
    return tuple(out), bad


def summarize(values: Sequence[ValueSample]) -> ValueSummary | None:
    if not values:
        return None
    first, last = values[0].value, values[-1].value
    nums = [s.value for s in values]
    return ValueSummary(
        since=values[0].ts,
        until=values[-1].ts,
        first=first,
        last=last,
        change=last - first,
        change_pct=(last - first) / first * HUNDRED if first != ZERO else None,
        high=max(nums),
        low=min(nums),
        count=len(values),
    )


def segments(values: Sequence[ValueSample]) -> list[tuple[ValueSample, ...]]:
    """Tramos sin huecos: se corta donde entre dos muestras pasan más de dos
    intervalos (el daemon estuvo parado); los huecos no se interpolan."""
    out: list[list[ValueSample]] = []
    for s in values:
        if out and s.ts - out[-1][-1].ts <= 2 * s.every_ms:
            out[-1].append(s)
        else:
            out.append([s])
    return [tuple(seg) for seg in out]


def thin(values: Sequence[ValueSample], n: int) -> tuple[ValueSample, ...]:
    """A lo sumo `n` muestras repartidas, con la primera y la última: plotext no
    distingue más puntos que columnas."""
    if len(values) <= n:
        return tuple(values)
    last = len(values) - 1
    picks = sorted({round(i * last / (n - 1)) for i in range(n)})
    return tuple(values[i] for i in picks)


def _jumped(a: Decimal, b: Decimal) -> bool:
    if a == ZERO:
        return b != ZERO
    return abs(b - a) / a * HUNDRED > JUMP_PCT


def last_stretch(values: Sequence[ValueSample]) -> tuple[ValueSample, ...]:
    """Las muestras desde el último salto de más de `JUMP_PCT` entre dos
    seguidas, en el total o en un venue: una transferencia en tránsito aplasta
    la escala y no deja ver lo que mueven el precio y el bot (D-69)."""
    start = 0
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        pairs = [(prev.value, cur.value)]
        before = {v.venue: v.value for v in prev.venues}
        pairs += [(before[v.venue], v.value) for v in cur.venues if v.venue in before]
        if any(_jumped(a, b) for a, b in pairs):
            start = i
    return tuple(values[start:])
