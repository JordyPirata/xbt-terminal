"""Lo puro del banco de pruebas (RF-34, RF-35, D-80): cómo casa una orden contra
el book grabado y el informe a partir de las filas del journal.

Spec: RF-34, RF-35, CA-19, CA-20 · design.md §6.14 · Tarea: T-87
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .models import Level, Side

SATS = Decimal(100_000_000)
MINUTE_MS = 60_000
HUNDRED = Decimal(100)
HANGING = "hanging_leg"  # así empieza el motivo de una parada por pierna colgada


def match(
    side: Side,
    price: Decimal,
    qty: Decimal,
    levels: Iterable[Level],
    taken: Callable[[Level], bool],
) -> list[tuple[Decimal, Decimal]]:
    """(precio, cantidad) que casa una orden taker contra `levels`, del mejor al
    peor, sin pasar de su límite. `taken`: el competidor se lleva ese nivel."""
    fills: list[tuple[Decimal, Decimal]] = []
    left = qty
    for level in levels:
        beyond = level.price > price if side == "buy" else level.price < price
        if beyond or left <= 0:
            break
        if taken(level):
            continue
        got = min(left, level.qty)
        fills.append((level.price, got))
        left -= got
    return fills


@dataclass(frozen=True, slots=True)
class ReplayReport:
    minutes: Decimal
    crossings: int
    fires: int
    filled: int
    capture_pct: Decimal | None  # None sin cruces
    net: Decimal  # BTC
    misses: int
    miss_per_100: Decimal | None  # None sin disparos
    hanging: int
    hanging_per_100: Decimal | None
    halted: str | None  # por qué quedó parado el bot, si no fue una colgada


def _share(part: int, whole: int) -> Decimal | None:
    return Decimal(part) * HUNDRED / whole if whole else None


def summarize(
    rows: Iterable[Mapping[str, Any]],
    *,
    crossings: int,
    filled: int,
    net: Decimal,
    duration_ms: int,
) -> ReplayReport:
    fires = misses = hanging = 0
    halted: str | None = None
    for row in rows:
        event, data = row.get("event"), row.get("data") or {}
        fires += event == "bot_fire"
        misses += event == "bot_miss"
        if event != "bot_state":
            continue
        reason = data.get("reason") or ""
        stopped = data.get("status") == "halted"
        hanging += stopped and reason.startswith(HANGING)
        halted = reason if stopped and not reason.startswith(HANGING) else None
    return ReplayReport(
        minutes=Decimal(duration_ms) / MINUTE_MS,
        crossings=crossings,
        fires=fires,
        filled=filled,
        capture_pct=_share(filled, crossings),
        net=net,
        misses=misses,
        miss_per_100=_share(misses, fires),
        hanging=hanging,
        hanging_per_100=_share(hanging, fires),
        halted=halted,
    )


def _rate(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def render(report: ReplayReport) -> str:
    sats = (report.net * SATS).to_integral_value()
    lines = [
        f"grabación: {report.minutes:.1f} min".replace(".0 min", " min"),
        f"cruces sobre el umbral: {report.crossings}",
        f"disparos: {report.fires}",
        f"dobles llenas: {report.filled}",
        f"tasa de captura: {_rate(report.capture_pct)} %",
        f"neta realizada: {report.net.quantize(Decimal('0.00000001')):f} BTC ({sats:f} sats)",
        f"bot_miss por 100 disparos: {_rate(report.miss_per_100)} ({report.misses})",
        f"piernas colgadas por 100 disparos: {_rate(report.hanging_per_100)} ({report.hanging})",
    ]
    if report.halted is not None:
        lines.append(f"el bot quedó parado: {report.halted}")
    return "\n".join(lines)
