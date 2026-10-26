"""Latencia del bot (T-72, RNF-23, D-70): la decisión, el top del book y el
camino de un cambio de book a `prepare_double`, con books profundos.

Los presupuestos de RNF-23 tienen margen amplio para que la suite cargada no
los rompa; cada test imprime lo medido (verlo con `pytest -s`)."""

import statistics
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from btcb2_terminal.core.book import L2Book
from btcb2_terminal.core.bot import BotLimits, BotState, Fire, Skip, VenueView, arm, decide
from btcb2_terminal.core.models import BookSnapshot, ConnState, D, Level

from ..fakes import spec
from ..services.test_bot_service import BOT, BotRig

NOW = 1_800_000_000_000
LEVELS = 50
STEP = D("0.00000010")
DECIDE_BUDGET_MS = 5.0
TOP_BUDGET_MS = 2.0
FIRE_BUDGET_MS = 50.0


def median_ms(work, runs: int = 30) -> float:
    work()  # calentar
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        work()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000


def side(start: Decimal, n: int, up: bool, qty: str = "10") -> tuple[Level, ...]:
    return tuple(Level(start + STEP * i if up else start - STEP * i, D(qty)) for i in range(n))


def deep_views(nx_bid: str, qty: str = "10") -> dict[str, VenueView]:
    """NonKYC vende desde 0.007 y Neoxa compra desde `nx_bid`, 50 niveles."""
    nk_ask, bid = D("0.00700000"), D(nx_bid)
    return {
        "nk": VenueView(
            spec("nk"), side(nk_ask - STEP * 100, LEVELS, False), side(nk_ask, LEVELS, True, qty),
            D("10"), D("10000"),
        ),
        "nx": VenueView(
            spec("nx"), side(bid, LEVELS, False, qty), side(bid + STEP * 100, LEVELS, True),
            D("10"), D("10000"),
        ),
    }  # fmt: skip


WALK = BotLimits(
    min_net_pct=D("0.5"),
    max_slippage_pct=D("0.2"),
    max_qty=D("100000"),
    fill_timeout_ms=3000,
    cooldown_s=10,
    max_trades_per_hour=20,
    max_daily_btc=D("100"),
    max_consecutive_failures=3,
    max_walk_pct={"nk": D("1"), "nx": D("1")},  # todos los niveles son referencia
)


@pytest.mark.spec("RNF-23", "RNF-22", "RF-20")
class TestDecide:
    @pytest.mark.parametrize(
        "case,nx_bid,qty,want",
        [
            ("cruce con walk", "0.00760000", "10", Fire),
            ("sin cruce con walk", "0.00690000", "10", Skip),
            # todos los pares por encima del umbral y poca profundidad: sin poda posible
            ("todos los pares rinden", "0.00900000", "0.0001", Fire),
        ],
    )
    def test_decide_con_50_niveles(self, case, nx_bid, qty, want):
        views, state = deep_views(nx_bid, qty), arm(BotState())
        assert isinstance(decide(views, state, WALK, NOW), want)
        ms = median_ms(lambda: decide(views, state, WALK, NOW))
        print(f"\ndecide · {case}: {ms:.3f} ms")
        assert ms < DECIDE_BUDGET_MS


@pytest.mark.spec("RNF-23", "RF-02")
def test_top_de_un_book_de_1000_niveles():
    book = L2Book("nk")
    bids = tuple(Level(D("0.004") - D("0.00000001") * i, D(1)) for i in range(1000))
    asks = tuple(Level(D("0.0041") + D("0.00000001") * i, D(1)) for i in range(1000))
    book.apply(BookSnapshot("nk", bids, asks, 1, None))
    ms = median_ms(lambda: book.top(LEVELS))
    print(f"\ntop(50) de 1000 niveles: {ms:.3f} ms")
    assert ms < TOP_BUDGET_MS


@pytest.mark.spec("RNF-23", "RF-19", "RF-20")
async def test_de_un_cruce_a_prepare_double(tmp_path):
    limits = replace(BOT, max_walk_pct={"nk": D("1"), "nx": D("1")})
    async with BotRig(tmp_path, limits=limits) as rig:
        sent: list[float] = []
        real = rig.orders.prepare_double

        async def timed(cmd):
            sent.append(time.perf_counter())
            return await real(cmd)

        rig.orders.prepare_double = timed
        rig.bot.arm()
        times = []
        for i in range(10):
            await rig.until(lambda: rig.bot.state.status == "armed", timeout=5)
            now = rig.clock.now
            nk = BookSnapshot(
                "nk", side(D("0.00690000"), LEVELS, False), side(D("0.00700000"), LEVELS, True), now
            )
            nx = BookSnapshot(
                "nx", side(D("0.00720000"), LEVELS, False), side(D("0.00730000"), LEVELS, True), now
            )
            t0 = time.perf_counter()
            for vid, snap in (("nk", nk), ("nx", nx)):
                rig.feeds[vid].push(ConnState(vid, "market", "ws", "up", 10.0, now), snap)
            await rig.until(lambda i=i: len(sent) > i, timeout=5)
            times.append((sent[i] - t0) * 1000)
            await rig.until(lambda: rig.bot.state.status == "cooldown", timeout=5)
            rig.clock.now += 60_000  # fin del enfriamiento
        ms = statistics.median(times)
        print(f"\ncruce → prepare_double: mediana {ms:.2f} ms (máx {max(times):.2f})")
        assert ms < FIRE_BUDGET_MS
