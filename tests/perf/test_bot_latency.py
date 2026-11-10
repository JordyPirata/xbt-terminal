"""Bot latency (T-72, RNF-23, D-70): the decision, the book top and the path from a book
change to `prepare_double`, with deep books.

RNF-23 budgets have ample margin so a loaded suite does not break them; each test
prints what it measured (see it with `pytest -s`)."""

import statistics
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from xbt_terminal.core.book import L2Book
from xbt_terminal.core.bot import BotLimits, BotState, Fire, Skip, VenueView, arm, decide
from xbt_terminal.core.models import BookSnapshot, ConnState, D, Level

from ..fakes import spec
from ..services.test_bot_service import BOT, BotRig

NOW = 1_800_000_000_000
LEVELS = 50
STEP = D("0.00000010")
DECIDE_BUDGET_MS = 5.0
TOP_BUDGET_MS = 2.0
FIRE_BUDGET_MS = 50.0


def median_ms(work, runs: int = 30) -> float:
    work()  # warm up
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        work()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000


def side(start: Decimal, n: int, up: bool, qty: str = "10") -> tuple[Level, ...]:
    return tuple(Level(start + STEP * i if up else start - STEP * i, D(qty)) for i in range(n))


def deep_views(nx_bid: str, qty: str = "10") -> dict[str, VenueView]:
    """NonKYC sells from 0.007 and Neoxa buys from `nx_bid`, 50 levels."""
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
    max_walk_pct={"nk": D("1"), "nx": D("1")},  # all levels are references
)


@pytest.mark.spec("RNF-23", "RNF-22", "RF-20")
class TestDecide:
    @pytest.mark.parametrize(
        "case,nx_bid,qty,want",
        [
            ("cruce con walk", "0.00760000", "10", Fire),
            ("sin cruce con walk", "0.00690000", "10", Skip),
            # all pairs above the threshold and little depth: no pruning possible
            ("todos los pares rinden", "0.00900000", "0.0001", Fire),
        ],
    )
    def test_decide_with_50_levels(self, case, nx_bid, qty, want):
        views, state = deep_views(nx_bid, qty), arm(BotState())
        assert isinstance(decide(views, state, WALK, NOW), want)
        ms = median_ms(lambda: decide(views, state, WALK, NOW))
        print(f"\ndecide · {case}: {ms:.3f} ms")
        assert ms < DECIDE_BUDGET_MS


@pytest.mark.spec("RNF-23", "RF-02")
def test_top_of_1000_level_book():
    book = L2Book("nk")
    bids = tuple(Level(D("0.004") - D("0.00000001") * i, D(1)) for i in range(1000))
    asks = tuple(Level(D("0.0041") + D("0.00000001") * i, D(1)) for i in range(1000))
    book.apply(BookSnapshot("nk", bids, asks, 1, None))
    ms = median_ms(lambda: book.top(LEVELS))
    print(f"\ntop(50) de 1000 niveles: {ms:.3f} ms")
    assert ms < TOP_BUDGET_MS


@pytest.mark.spec("RNF-23", "RF-19", "RF-20")
async def test_from_cross_to_prepare_double(tmp_path):
    limits = replace(BOT, max_walk_pct={"nk": D("1"), "nx": D("1")})
    async with BotRig(tmp_path, limits=limits) as rig:
        sent: list[float] = []
        real = rig.orders.prepare_double

        async def timed(command):
            sent.append(time.perf_counter())
            return await real(command)

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
            for venue_id, snap in (("nk", nk), ("nx", nx)):
                rig.feeds[venue_id].push(ConnState(venue_id, "market", "ws", "up", 10.0, now), snap)
            await rig.until(lambda i=i: len(sent) > i, timeout=5)
            times.append((sent[i] - t0) * 1000)
            await rig.until(lambda: rig.bot.state.status == "cooldown", timeout=5)
            rig.clock.now += 60_000  # end of the cooldown
        ms = statistics.median(times)
        print(f"\ncruce → prepare_double: mediana {ms:.2f} ms (máx {max(times):.2f})")
        assert ms < FIRE_BUDGET_MS
