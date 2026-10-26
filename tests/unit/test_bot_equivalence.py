"""La decisión del bot no cambia al bajar su complejidad (T-72, D-70): `decide`
de `core/bot.py` y el oráculo congelado (`bot_reference.py`) dan el mismo
`Fire` o `Skip`, `detail` incluido, en miles de casos aleatorios con semilla
fija."""

import random
from dataclasses import replace
from decimal import Decimal

import pytest

from btcb2_terminal.core.bot import BotLimits, BotState, VenueView, arm, decide
from btcb2_terminal.core.inventory import InventoryTarget
from btcb2_terminal.core.models import Level, MarketSpec

from . import bot_reference as ref

NOW = 1_800_000_000_000
CASES = 3000
TICK = Decimal("0.00000001")


def _spec(rnd: random.Random, venue: str) -> MarketSpec:
    lot = rnd.choice(
        [Decimal("0.0001"), Decimal("0.00000001"), Decimal("0.001"), Decimal("0.0003")]
    )
    return MarketSpec(
        venue=venue,  # type: ignore[arg-type]
        symbol="X",
        base="BTCB2",
        quote="BTC",
        tick_size=rnd.choice([TICK, TICK * 10, TICK * 100]),
        lot_size=lot,
        min_qty=lot * rnd.choice([1, 1, 5, 100]),
        min_notional=rnd.choice([Decimal(0), Decimal(0), Decimal("0.0001"), Decimal("0.01")]),
        maker_fee=Decimal("0.002"),
        taker_fee=rnd.choice([Decimal("0.002"), Decimal("0.001"), Decimal("0.003")]),
    )


def _levels(rnd: random.Random, start: Decimal, n: int, up: bool) -> tuple[Level, ...]:
    """`n` niveles distintos desde `start`, hacia arriba (asks) o abajo (bids)."""
    out, price = [], start
    for _ in range(n):
        if price <= 0:
            break
        qty = Decimal(rnd.choice(["0.0001", "0.001", "0.05", "0.3", "1", "7", "40", "250"]))
        out.append(Level(price, qty))
        step = TICK * rnd.choice([1, 1, 2, 5, 13, 40])
        price = price + step if up else price - step
    return tuple(out)


def _view(rnd: random.Random, venue: str, mid: Decimal) -> VenueView:
    half = TICK * rnd.choice([1, 3, 20, 100, 400])
    bids = _levels(rnd, mid - half, rnd.choice([0, 1, 2, 5, 20, 50]), up=False)
    asks = _levels(rnd, mid + half, rnd.choice([0, 1, 2, 5, 20, 50]), up=True)
    return VenueView(
        spec=_spec(rnd, venue),
        bids=bids,
        asks=asks,
        quote_available=Decimal(rnd.choice(["0", "0.00001", "0.001", "0.05", "10"])),
        base_available=Decimal(rnd.choice(["0", "0.001", "0.5", "20", "10000"])),
        blocked=rnd.choice([None] * 25 + ["book viejo"]),
        quote_total=Decimal(rnd.choice(["0", "0.01", "0.1", "1"])),
        base_total=Decimal(rnd.choice(["0", "1", "20", "200"])),
        mid=rnd.choice([None, mid]),
        net_fee=rnd.choice([None, None, Decimal("0.0015"), Decimal("0.001")]),
    )


def _case(seed: int):
    rnd = random.Random(seed)
    base = Decimal("0.00450000")
    # con el signo del hueco cruzan en un sentido, en el otro o en ninguno
    gap = TICK * rnd.choice([-30000, -10000, -3000, -500, 0, 60, 900, 3000, 10000, 30000])
    views = {"nk": _view(rnd, "nk", base), "nx": _view(rnd, "nx", base + gap)}
    walk = {
        vid: Decimal(rnd.choice(["0", "0", "0.1", "0.5", "0.8", "2"]))
        for vid in ("nk", "nx")
        if rnd.random() < 0.7
    }
    limits = BotLimits(
        min_net_pct=Decimal(rnd.choice(["-5", "-1", "0", "0.1", "0.5", "1", "3"])),
        max_slippage_pct=Decimal(rnd.choice(["0", "0.05", "0.2", "1"])),
        max_qty=Decimal(rnd.choice(["0.0001", "0.01", "1", "100", "100000"])),
        fill_timeout_ms=3000,
        cooldown_s=10,
        max_trades_per_hour=20,
        max_daily_btc=Decimal(rnd.choice(["0.00001", "0.001", "1", "100"])),
        max_consecutive_failures=3,
        max_walk_pct=walk,
    )
    target = rnd.choice(
        [None, InventoryTarget(Decimal(25), Decimal(10)), InventoryTarget(Decimal(50), Decimal(1))]
    )
    return views, limits, target


@pytest.mark.spec("RF-19", "RF-20", "RF-26", "RNF-22", "RNF-23")
@pytest.mark.parametrize("chunk", range(10))
def test_misma_decision_que_el_oraculo(chunk):
    state = arm(BotState())
    fires = 0
    for seed in range(chunk * CASES // 10, (chunk + 1) * CASES // 10):
        views, limits, target = _case(seed)
        got = decide(views, state, limits, NOW, target)
        want = ref.decide(views, state, limits, NOW, target)
        assert got == want, f"semilla {seed}"
        fires += type(got).__name__ == "Fire"
    assert fires > 0  # los casos también disparan, no solo descartan


@pytest.mark.spec("RF-19", "RF-20", "RNF-22")
def test_book_desordenado_misma_decision():
    """Sin orden no hay monotonía: se prueban todos los pares, como antes."""
    state = arm(BotState())
    for seed in range(600):
        views, limits, target = _case(seed)
        rnd = random.Random(-seed)
        views = {
            vid: replace(
                v,
                asks=tuple(rnd.sample(v.asks, len(v.asks))),
                bids=tuple(rnd.sample(v.bids, len(v.bids))),
            )
            for vid, v in views.items()
        }
        got = decide(views, state, limits, NOW, target)
        assert got == ref.decide(views, state, limits, NOW, target), f"semilla {seed}"
