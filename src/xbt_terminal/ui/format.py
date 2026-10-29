"""Formatos de sats y % de la pantalla: un solo sitio para todos (D-77).

`None` sale como "—": un valor que no se pudo calcular no es un cero.

Spec: RF-04, RF-25, RF-29 · Tareas: T-81
"""

from __future__ import annotations

from decimal import Decimal

from ..core.cross import sats

MISSING = "—"


def fmt_sats(btc: Decimal | None, *, signed: bool = True) -> str:
    """Sats enteros, sin unidad: "+1234"."""
    if btc is None:
        return MISSING
    return f"{sats(btc):{'+' if signed else ''}.0f}"


def fmt_sats_grouped(btc: Decimal | None) -> str:
    """Sats con los miles separados por espacio y unidad: "1 234 567 sats"."""
    if btc is None:
        return MISSING
    return f"{sats(btc):,.0f} sats".replace(",", " ")


def fmt_pct(value: Decimal | None, *, decimals: int = 2, signed: bool = True) -> str:
    """Porcentaje con unidad: "+1.23 %"."""
    if value is None:
        return MISSING
    return f"{value:{'+' if signed else ''}.{decimals}f} %"


def fmt_share(value: Decimal | None) -> str:
    """Parte del inventario, sin signo y con un decimal: "27.7 %" (RF-29)."""
    return fmt_pct(value, decimals=1, signed=False)


def fmt_btc_sats_pct(value: Decimal, pct: Decimal, *, signed: bool) -> str:
    """BTC · sats · %, con el % que ya calcula `core/cross.py`."""
    sign = "+" if signed else ""
    return f"{value:{sign}.8f} · {sats(value):{sign}.0f} sats · {pct:{sign}.2f} %"
