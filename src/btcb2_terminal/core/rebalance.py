"""Plan de reequilibrio entre venues (RF-29, D-53).

Una sola transferencia deja los dos venues con el mismo % de BTCB2: mandar
BTCB2 del venue con más % al otro, o BTC del que tiene menos % al otro. Se
calculan las dos y se recomienda la viable más barata. La comisión depende del
venue (D-53): aparte (se pide X, llega X y salen X + comisión, Neoxa) o
descontada (se pide X, sale X y llega X - comisión). Una transferencia no
cambia el % total: eso se corrige operando, a mano.

Solo calcula. La terminal no retira ni transfiere.

Spec: RF-29, CA-14 · design.md §6.12 · Tarea: T-45
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .inventory import BASE, HUNDRED, QUOTE, base_pct
from .models import ZERO, Balance, VenueId, WithdrawalFee, asset_label

STEP = Decimal("0.00000001")  # los dos venues retiran con 8 decimales

FeeInfo = WithdrawalFee | str  # str: por qué no llegó la comisión


@dataclass(frozen=True, slots=True)
class Holding:
    """Saldos de un venue: totales para el %, disponibles para lo que se puede
    enviar; `price` es el mid del venue."""

    btc: Decimal
    btcb2: Decimal
    btc_free: Decimal
    btcb2_free: Decimal
    price: Decimal

    @property
    def pct(self) -> Decimal | None:
        return base_pct(self.btc, self.btcb2, self.price)


@dataclass(frozen=True, slots=True)
class Transfer:
    asset: str
    source: VenueId
    dest: VenueId
    amount: Decimal  # lo que se escribe en el formulario de retiro del venue
    fee: Decimal | None  # None: la comisión no llegó
    received: Decimal  # lo que llega al destino
    debited: Decimal  # lo que sale del origen
    cost_btc: Decimal | None  # la comisión en BTC, al mid del origen
    after: Mapping[VenueId, Decimal | None]  # % de BTCB2 de cada venue después
    problem: str = ""  # vacío si es viable

    @property
    def viable(self) -> bool:
        return not self.problem


@dataclass(frozen=True, slots=True)
class Plan:
    before: Mapping[VenueId, Decimal | None]
    total_pct: Decimal | None
    transfers: tuple[Transfer, ...]

    @property
    def best(self) -> Transfer | None:
        viable = [t for t in self.transfers if t.viable and t.cost_btc is not None]
        return min(viable, key=lambda t: t.cost_btc or ZERO) if viable else None


def holdings(
    balances: Mapping[VenueId, Mapping[str, Balance] | None],
    mids: Mapping[VenueId, Decimal | None],
) -> dict[VenueId, Holding] | str:
    """Los `Holding` de cada venue, o por qué no se pueden calcular."""
    out: dict[VenueId, Holding] = {}
    for vid, bal in balances.items():
        if bal is None:
            return f"{vid}: sin saldos todavía"
        price = mids.get(vid)
        if price is None:
            return f"{vid}: sin mid para valorar"
        btc, btcb2 = bal.get(QUOTE), bal.get(BASE)
        out[vid] = Holding(
            btc.total if btc else ZERO,
            btcb2.total if btcb2 else ZERO,
            btc.available if btc else ZERO,
            btcb2.available if btcb2 else ZERO,
            price,
        )
    return out


def plan(
    holdings: Mapping[VenueId, Holding], fees: Mapping[tuple[VenueId, str], FeeInfo]
) -> Plan | str:
    """Las dos alternativas; A es el venue con más % y B el otro."""
    if len(holdings) != 2:
        return "hacen falta los saldos de los dos venues"
    (va, ha), (vb, hb) = sorted(holdings.items(), key=lambda kv: kv[1].pct or ZERO, reverse=True)
    value = sum((h.btc + h.btcb2 * h.price for h in holdings.values()), ZERO)
    base_value = sum((h.btcb2 * h.price for h in holdings.values()), ZERO)
    return Plan(
        before={vid: h.pct for vid, h in holdings.items()},
        total_pct=base_value / value * HUNDRED if value > 0 else None,
        transfers=(
            _base_transfer(va, ha, vb, hb, fees.get((va, BASE), "sin datos")),
            _quote_transfer(va, ha, vb, hb, fees.get((vb, QUOTE), "sin datos")),
        ),
    )


def _split(info: FeeInfo) -> tuple[Decimal, Decimal]:
    """(lo que paga de más el origen, lo que pierde el destino)."""
    if not isinstance(info, WithdrawalFee):
        return ZERO, ZERO
    return (info.fee, ZERO) if info.on_top else (ZERO, info.fee)


def _base_transfer(va: VenueId, a: Holding, vb: VenueId, b: Holding, info: FeeInfo) -> Transfer:
    """BTCB2 de A a B: (b_A - x - fo)·p_A·q_B = (b_B + x - fd)·p_B·q_A, con fo
    la comisión aparte y fd la descontada (una de las dos es 0)."""
    fo, fd = _split(info)
    den = a.price * b.btc + b.price * a.btc
    x = ((a.btcb2 - fo) * a.price * b.btc - (b.btcb2 - fd) * b.price * a.btc) / den if den else ZERO
    amount = _down(x)
    debited, received = amount + fo, amount - fd
    after = {
        va: base_pct(a.btc, a.btcb2 - debited, a.price),
        vb: base_pct(b.btc, b.btcb2 + received, b.price),
    }
    cost = info.fee * a.price if isinstance(info, WithdrawalFee) else None
    problem = (
        "sin BTC en ningún venue"
        if not den
        else _problem(info, amount, debited, a.btcb2_free, BASE, va, a.pct == b.pct)
    )
    return _transfer(BASE, va, vb, amount, info, received, debited, cost, after, problem)


def _quote_transfer(va: VenueId, a: Holding, vb: VenueId, b: Holding, info: FeeInfo) -> Transfer:
    """BTC de B a A: b_A·p_A·(q_B - y - fo) = b_B·p_B·(q_A + y - fd)."""
    fo, fd = _split(info)
    den = a.btcb2 * a.price + b.btcb2 * b.price
    y = (a.btcb2 * a.price * (b.btc - fo) - b.btcb2 * b.price * (a.btc - fd)) / den if den else ZERO
    amount = _down(y)
    debited, received = amount + fo, amount - fd
    after = {
        va: base_pct(a.btc + received, a.btcb2, a.price),
        vb: base_pct(b.btc - debited, b.btcb2, b.price),
    }
    cost = info.fee if isinstance(info, WithdrawalFee) else None
    problem = (
        f"sin {asset_label(BASE)} en ningún venue"
        if not den
        else _problem(info, amount, debited, b.btc_free, QUOTE, vb, a.pct == b.pct)
    )
    return _transfer(QUOTE, vb, va, amount, info, received, debited, cost, after, problem)


def _transfer(
    asset: str,
    source: VenueId,
    dest: VenueId,
    amount: Decimal,
    info: FeeInfo,
    received: Decimal,
    debited: Decimal,
    cost: Decimal | None,
    after: Mapping[VenueId, Decimal | None],
    problem: str,
) -> Transfer:
    fee = info.fee if isinstance(info, WithdrawalFee) else None
    return Transfer(asset, source, dest, amount, fee, received, debited, cost, after, problem)


def _problem(
    info: FeeInfo,
    amount: Decimal,
    debited: Decimal,
    free: Decimal,
    asset: str,
    source: VenueId,
    same: bool,
) -> str:
    """Por qué no es viable, o vacío. Con el mismo % antes, la comisión sola
    daría una cantidad: no hace falta nada."""
    label = asset_label(asset)
    if same:
        return "no hace falta: ya tienen el mismo %"
    if isinstance(info, str):
        return f"sin comisión de retiro: {info}" if amount > 0 else "no hace falta"
    if amount <= 0:  # con la comisión aparte, lo que pagaría el origen ya pasa del medio
        return f"la comisión ({info.fee:f} {label}) es mayor que lo que hay que mover"
    if not info.active:
        return f"retiro de {label} cerrado en {source}"
    if debited > free:
        return f"hacen falta {debited:f} {label} y hay {free:f} disponible en {source}"
    if info.minimum is not None and amount < info.minimum:
        return f"{amount:f} {label} está bajo el mínimo de retiro ({info.minimum:f})"
    if not info.on_top and info.fee >= amount:
        return f"la comisión ({info.fee:f} {label}) se come el envío"
    return ""


def _down(value: Decimal) -> Decimal:
    return value.quantize(STEP, rounding=ROUND_DOWN) if value > 0 else ZERO


def break_even(cost_btc: Decimal, qty: Decimal, price: Decimal, net_pct: Decimal) -> int | None:
    """Dobles de `qty` a la neta `net_pct` que pagan `cost_btc`; None si no ganan."""
    per_double = qty * price * net_pct / HUNDRED
    if per_double <= 0:
        return None
    return math.ceil(cost_btc / per_double)
