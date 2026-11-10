"""BotService in dry-run (T-36, RF-19 to RF-21, RF-23, CA-08, CA-09): hub with fake
venues, DryRunVenue with fixed balances, account, orders and bot, all real."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from xbt_terminal.adapters.base import UncertainError, VenueError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.config import BotConfig
from xbt_terminal.core.bot import DAY_MS, BotLimits, FeeToken
from xbt_terminal.core.double import start
from xbt_terminal.core.inventory import InventoryTarget
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, BookSnapshot, ConnState, D, Level, OrderRequest
from xbt_terminal.core.risk import RiskLimits
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.account import AccountService
from xbt_terminal.services.bot import BotService
from xbt_terminal.services.faults import Faults
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import OrderService

from ..fakes import T0, FakeFeed

NOW = T0 + 60_000
RISK = RiskLimits(
    max_order_pct=D("100"),
    max_mid_deviation_pct=D("5"),
    max_venue_exposure=D("100"),
    stale_after_s=15,
    hanging_leg_after_s=30,
    max_clock_skew_s=5,
)
BOT = BotLimits(
    min_net_pct=D("0.5"),
    max_slippage_pct=D("0.2"),
    max_qty=D("10"),
    fill_timeout_ms=100,
    cooldown_s=60,
    max_trades_per_hour=20,
    max_daily_btc=D("1"),
    max_consecutive_failures=3,
)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class BotRig:
    def __init__(
        self, tmp_path, *, live=False, enabled=True, limits=BOT, risk=RISK, wire=True,
        fee_tokens=None, nk_extra=None,
    ):  # fmt: skip
        self.clock = Clock()
        self.feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.faults = Faults()
        self.logs = tmp_path / "logs"
        self.journal = Journal(self.logs, "live" if live else "dry-run", Redactor([]))
        self.hub = MarketHub(
            self.feeds, history=50, retry_s=0.01, clock=self.clock, journal=self.journal
        )
        bal = {"BTC": Balance("BTC", D("1")), "BTCB2": Balance("BTCB2", D("1000"))}
        own = {"nk": bal | (nk_extra or {}), "nx": bal}
        self.venues = {
            venue_id: DryRunVenue(
                feed,
                own[venue_id],
                spec=lambda venue_id=venue_id: self.hub.state[venue_id].spec,
                clock=self.clock,
            )
            for venue_id, feed in self.feeds.items()
        }  # fmt: skip
        self.account = AccountService(
            self.venues, journal=self.journal, retry_s=0.01, clock=self.clock, faults=self.faults
        )
        self.orders = OrderService(
            self.hub, self.venues, self.journal, risk, account=self.account,
            faults=self.faults, clock=self.clock, live=live,
        )  # fmt: skip
        if wire:  # without `wire`, the Engine must do the link (D-61)
            self.account.subscribe_events(self.orders.on_private_event)
        self.fee_tokens = fee_tokens or {}
        self.bot = self.new_bot(live=live, enabled=enabled, limits=limits)
        self.tasks: list[asyncio.Task] = []

    def new_bot(self, *, live=False, enabled=True, limits=BOT):
        return BotService(
            self.hub, self.orders, self.account, self.journal, BotConfig(enabled, limits),
            live=live, faults=self.faults, clock=self.clock, settle_s=1.0, tick_s=0.02,
            fee_tokens=self.fee_tokens,
        )  # fmt: skip

    async def __aenter__(self):
        for work in (self.hub.run(), self.account.run(), self.bot.run()):
            self.tasks.append(asyncio.create_task(work))
        await self.until(
            lambda: all(market_state.ready for market_state in self.hub.state.values())
        )
        await self.until(
            lambda: all(market_state.balances for market_state in self.account.state.values())
        )
        return self

    async def __aexit__(self, *error):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.journal.close()

    async def until(self, predicate, timeout=2.0):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.005)

    def books(self, nk=("0.00690000", "0.00700000"), nx=("0.00720000", "0.00730000")):
        """They cross by default: buy on NonKYC at 0.007 and sell on Neoxa at 0.0072."""
        for venue_id, (bid, ask) in (("nk", nk), ("nx", nx)):
            snap = BookSnapshot(
                venue_id, (Level(D(bid), D("50")),), (Level(D(ask), D("50")),), self.clock.now
            )
            self.feeds[venue_id].push(
                ConnState(venue_id, "market", "ws", "up", 10.0, self.clock.now), snap
            )

    def events(self, name=None):
        rows = [
            json.loads(line)
            for file in sorted(self.logs.glob("journal-*.jsonl"))
            for line in file.read_text().splitlines()
        ]
        return [row for row in rows if name is None or row["event"] == name]

    async def open_orders(self):
        return {venue_id: await venue.open_orders() for venue_id, venue in self.venues.items()}


@pytest.fixture
async def rig(tmp_path):
    async with BotRig(tmp_path) as bot_rig:
        yield bot_rig


@pytest.mark.spec("RF-19", "RF-20", "RF-23", "CA-08")
class TestFire:
    async def test_one_cross_one_double(self, rig):
        assert rig.bot.arm() is None
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        rig.books()  # the cross stays, but it is cooling down
        await asyncio.sleep(0.1)
        assert len(rig.orders.doubles) == 1
        (double,) = rig.orders.doubles.values()
        assert (double.buy.req.venue, double.buy.req.price) == ("nk", D("0.00701400"))
        assert (double.sell.req.venue, double.sell.req.price) == ("nx", D("0.00718560"))
        assert double.buy.req.qty == D("10")  # max_qty
        # dry-run does not fill: both are cancelled at fill_timeout_ms
        assert {order_leg.status for order_leg in double.legs} == {"cancelled"}
        assert await rig.open_orders() == {"nk": [], "nx": []}
        (fire,) = rig.events("bot_fire")
        assert fire["data"]["double"] == double.id and D(fire["data"]["qty"]) == 10
        assert fire["data"]["buy_venue"] == "nk" and fire["data"]["sell_venue"] == "nx"
        states = [entry["data"]["status"] for entry in rig.events("bot_state")]
        assert states == ["armed", "firing", "cooldown"]
        assert len(rig.events("order_sent")) == 2 and len(rig.events("cancel_sent")) == 2

    async def test_fires_again_after_cooldown(self, rig):
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        rig.clock.now += 60_000
        rig.books()
        await rig.until(lambda: len(rig.orders.doubles) == 2)

    async def test_both_filled_cools_down(self, rig):
        rig.bot.arm()
        rig.bot._limits = replace(BOT, fill_timeout_ms=2_000)
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (double,) = rig.orders.doubles.values()
        await rig.until(lambda: all(order_leg.order_id for order_leg in double.legs))
        for order_leg in double.legs:
            await rig.venues[order_leg.req.venue].simulate_fill(order_leg.order_id, D("10"))
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        assert rig.events("cancel_sent") == []

    async def test_without_cross_does_not_fire(self, rig):
        rig.bot.arm()
        rig.books(nx=("0.00700500", "0.00730000"))  # net < 0.5 %
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and rig.bot.state.status == "armed"
        assert rig.bot.last_skip.reason == "below_threshold"

    async def test_off_does_not_fire(self, rig):
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and rig.bot.state.status == "off"


@pytest.mark.spec("RF-19", "CA-08")
class TestBlocks:
    async def test_stale_book(self, rig):
        rig.books()
        await rig.until(lambda: rig.hub.state["nx"].book.valid)
        rig.clock.now += 16_000  # more than stale_after_s
        rig.bot.arm()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {}
        assert rig.bot.last_skip.reason == "blocked" and "book viejo" in rig.bot.last_skip.detail

    @pytest.mark.spec("RNF-24", "CA-18")
    async def test_ws_connected_unchanged_and_rest_different(self, rig):
        """d-9820a4: NonKYC's WS stayed "up" without sending changes and the bid the bot saw
        was gone. With the check, it does not fire until the new book."""
        rig.books()
        await rig.until(lambda: rig.hub.state["nx"].book.valid)
        rig.clock.now += 16_000
        for venue_id in ("nk", "nx"):  # connection heartbeat: no longer gives freshness
            rig.hub.apply(venue_id, ConnState(venue_id, "market", "ws", "up", 10.0, rig.clock.now))
        rig.feeds["nx"].rest_book = BookSnapshot(
            "nx", (Level(D("0.00720000"), D("50")),), (Level(D("0.00730000"), D("50")),), NOW
        )
        assert await rig.hub.check_book("nx") is True  # Neoxa confirms its book
        rig.feeds["nk"].rest_book = BookSnapshot(
            "nk", (Level(D("0.00690000"), D("50")),), (Level(D("0.00720000"), D("50")),), NOW
        )
        assert await rig.hub.check_book("nk") is False
        rig.bot.arm()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {}
        assert rig.bot.last_skip.reason == "blocked" and "book viejo" in rig.bot.last_skip.detail
        assert [row["venue"] for row in rig.events("book_resync")] == ["nk"]
        rig.books()  # the new book arrives and the cross stays
        await rig.until(lambda: len(rig.orders.doubles) == 1)

    @pytest.mark.spec("RNF-24", "CA-18")
    async def test_quiet_confirmed_book_fires_past_stale_after_s(self, rig):
        rig.books()
        await rig.until(lambda: rig.hub.state["nx"].book.valid)
        rig.clock.now += 300_000
        rig.feeds["nk"].rest_book = BookSnapshot(
            "nk", (Level(D("0.00690000"), D("50")),), (Level(D("0.00700000"), D("50")),), NOW
        )
        rig.feeds["nx"].rest_book = BookSnapshot(
            "nx", (Level(D("0.00720000"), D("50")),), (Level(D("0.00730000"), D("50")),), NOW
        )
        assert await rig.hub.check_book("nk") and await rig.hub.check_book("nx")
        rig.bot.arm()
        await rig.until(lambda: len(rig.orders.doubles) == 1)

    async def test_venue_failure(self, rig):
        rig.hub.state["nx"].error = "RuntimeError: bug"
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and "nx" in rig.bot.last_skip.detail

    async def test_account_with_error_without_text(self, rig):
        """D-51: on 2026-10-23 the bot fired with balances failing because the account error
        was "" (ConnectTimeout without text)."""
        rig.bot.arm()
        rig.account.state["nx"].error = ""
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {}
        assert rig.bot.last_skip.reason == "blocked" and "nx" in rig.bot.last_skip.detail

    async def test_market_with_error_without_text(self, rig):
        rig.hub.state["nk"].error = ""
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and "nk" in rig.bot.last_skip.detail

    async def test_uncertain_order_open(self, rig):
        rig.orders.uncertain["t-x"] = object()  # any uncertain order blocks
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and rig.bot.last_skip.reason == "uncertain_open"

    async def test_another_unfinished_double(self, rig):
        """A manual double open: the bot does not send another on top (one at a time)."""
        buy = OrderRequest("nk", "buy", D("1"), D("0.007"), "c-1")
        sell = OrderRequest("nx", "sell", D("1"), D("0.0072"), "c-2")
        rig.orders.doubles["d-manual"] = start("d-manual", buy, sell, NOW)
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert set(rig.orders.doubles) == {"d-manual"}
        assert rig.bot.last_skip.reason == "double_open"


@pytest.mark.spec("RF-19")
async def test_live_without_measured_skew_does_not_fire(tmp_path):
    async with BotRig(tmp_path, live=True) as rig:
        assert rig.bot.arm() is None
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and "reloj" in rig.bot.last_skip.detail


@pytest.mark.spec("RF-18")
async def test_live_needs_enabled(tmp_path):
    async with BotRig(tmp_path, live=True, enabled=False) as rig:
        assert "enabled" in rig.bot.arm()
        assert rig.bot.state.status == "off"
    async with BotRig(tmp_path / "dry", enabled=False) as rig:
        assert rig.bot.arm() is None  # in dry-run, always


@pytest.mark.spec("RF-18", "RF-20")
async def test_confirmation_shows_book_walk(tmp_path):
    async with BotRig(tmp_path) as rig:
        assert "recorre" not in rig.bot.summary
    walk = replace(BOT, max_walk_pct={"nk": D("0.8"), "nx": D("0")})
    async with BotRig(tmp_path / "walk", limits=walk) as rig:
        assert "recorre el book hasta NonKYC 0.8 % del mejor precio" in rig.bot.summary
        assert "Neoxa" not in rig.bot.summary.split("recorre")[1]


@pytest.mark.spec("RF-21", "CA-09")
class TestStop:
    async def test_leg_rejected_by_venue(self, rig):
        async def refuse(order_request):
            raise VenueError("Neoxa: [400] rechazada")

        rig.venues["nx"].place_limit = refuse
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("rejected")
        assert await rig.open_orders() == {"nk": [], "nx": []}  # the buy, cancelled
        (halt,) = [
            entry for entry in rig.events("bot_state") if entry["data"]["status"] == "halted"
        ]
        assert "rechazada" in halt["data"]["reason"]

    async def test_unconnected_leg_says_not_sent(self, rig):
        """D-51: unconnected, the order never left; the stop must not say "rechazada"."""

        async def offline(order_request):
            raise httpx.ConnectTimeout("")

        rig.venues["nx"].place_limit = offline
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert "no enviada, sin conexión (ConnectTimeout)" in rig.bot.state.reason
        (err,) = rig.events("order_error")
        assert err["venue"] == "nx" and err["data"]["error"] == (
            "no enviada, sin conexión (ConnectTimeout)"
        )

    async def test_uncertain_leg(self, rig):
        async def unclear(order_request):
            raise UncertainError("Neoxa: sin respuesta clara (ReadTimeout)")

        rig.venues["nx"].place_limit = unclear
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("uncertain")
        assert (await rig.open_orders())["nk"] == []

    async def test_unequal_fill_is_hanging_leg(self, rig):
        rig.bot.arm()
        rig.bot._limits = replace(BOT, fill_timeout_ms=300)
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (double,) = rig.orders.doubles.values()
        await rig.until(lambda: double.buy.order_id is not None)
        await rig.venues["nk"].simulate_fill(double.buy.order_id, D("4"))
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("hanging_leg")
        assert await rig.open_orders() == {"nk": [], "nx": []}
        rig.clock.now += 3_600_000
        rig.books()
        await asyncio.sleep(0.1)
        assert len(rig.orders.doubles) == 1  # stopped until re-armed by hand

    async def test_consecutive_risk_rejections(self, tmp_path):
        risk = replace(RISK, max_venue_exposure=D("0.0001"))
        async with BotRig(tmp_path, risk=risk, limits=replace(BOT, cooldown_s=2)) as rig:
            rig.bot.arm()
            for attempt in range(1, 4):
                rig.books()
                await rig.until(lambda attempt=attempt: len(rig.events("bot_fire")) == attempt)
                rig.clock.now += 2_000
            await rig.until(lambda: rig.bot.state.status == "halted")
            assert "3 disparos rechazados" in rig.bot.state.reason
            assert rig.events("order_sent") == []

    async def test_bug_stops_bot(self, rig):
        def broken(now):
            raise RuntimeError("bug en el bot")

        rig.bot._views = broken
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert "bug en el bot" in rig.bot.state.reason
        assert rig.faults.count == 1 and rig.faults.recent[0].where == "bot"
        del rig.bot._views
        await asyncio.sleep(0.1)
        assert rig.bot.state.status == "halted"  # does not re-arm by itself


@pytest.mark.spec("RF-22", "RF-23")
async def test_counters_rebuilt_from_journal(tmp_path):
    async with BotRig(tmp_path) as rig:
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        first = rig.bot.state
        again = rig.new_bot()  # as when restarting the terminal
        assert again.state.status == "off"
        assert again.state.fired_at == first.fired_at
        assert again.state.day == NOW // DAY_MS and again.state.day_btc == first.day_btc
        assert again.state.last_fire_at == first.last_fire_at


@pytest.mark.spec("RF-22")
class TestDailyCap:
    """T-46 (D-54): the daily cap stops the bot even when no shot uses it up."""

    async def test_less_than_a_lot_left(self, tmp_path):
        # the first shot spends 10 × 0.007014 = 0.07014 BTC and leaves 0.0000005, less than a
        # 0.0001 lot at that price
        limits = replace(BOT, max_daily_btc=D("0.0701405"))
        async with BotRig(tmp_path, limits=limits) as rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            rig.clock.now += 60_000
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "halted")
            assert rig.bot.state.reason.startswith("day_cap")
            assert len(rig.orders.doubles) == 1
            assert rig.events("bot_state")[-1]["data"]["status"] == "halted"

    async def test_rearming_same_day_with_cap_spent(self, rig):
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        rig.bot.disarm()
        rig.bot._limits = replace(BOT, max_daily_btc=D("0.05"))  # 0.07014 already spent
        rig.clock.now += 60_000
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("day_cap")
        assert "day_cap" in rig.bot.status_text()
        assert len(rig.orders.doubles) == 1

    async def test_after_restart_with_cap_spent(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            rig.bot.disarm()
            rig.clock.now += 60_000
            again = rig.new_bot(limits=replace(BOT, max_daily_btc=D("0.05")))
            rig.tasks.append(asyncio.create_task(again.run()))
            assert again.arm() is None
            rig.books()
            await rig.until(lambda: again.state.status == "halted")
            assert again.state.reason.startswith("day_cap")
            assert len(rig.orders.doubles) == 1


@pytest.mark.spec("RF-18", "RF-22")
async def test_turning_off_while_firing_leaves_it_off(tmp_path):
    """Seen for real (2026-10-22 03:34): bot off with a double in flight, and on closing
    it the bot went to cooldown and then re-armed by itself."""
    async with BotRig(tmp_path, limits=replace(BOT, cooldown_s=2)) as rig:
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "firing")
        rig.bot.disarm()
        await rig.until(lambda: rig.events("cancel_ack") and len(rig.events("cancel_ack")) == 2)
        await asyncio.sleep(0.1)
        assert rig.bot.state.status == "off"
        assert rig.bot.state.fired_at  # the shot counts for the caps
        rig.clock.now += 60_000
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.bot.state.status == "off" and len(rig.orders.doubles) == 1


@pytest.mark.spec("RF-21")
async def test_turned_off_while_firing_and_ending_badly_stays_halted(tmp_path):
    async def refuse(order_request):
        await asyncio.sleep(0.05)  # gives time to turn it off with the double in flight
        raise VenueError("Neoxa: [400] rechazada")

    async with BotRig(tmp_path) as rig:
        rig.venues["nx"].place_limit = refuse
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "firing")
        rig.bot.disarm()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("rejected")


@pytest.mark.spec("RF-26", "CA-12")
class TestInventoryRatio:
    """Rig balances: 1 BTC + 1000 BTCB2 per venue, ~87 % of the value in BTCB2."""

    def rig_with(self, tmp_path, target):
        rig = BotRig(tmp_path)
        rig.bot = BotService(
            rig.hub, rig.orders, rig.account, rig.journal, BotConfig(True, BOT),
            live=False, faults=rig.faults, clock=rig.clock, settle_s=1.0, tick_s=0.02,
            target=target,
        )  # fmt: skip
        return rig

    async def test_does_not_fire_if_it_worsens_a_venue_and_says_so(self, tmp_path):
        async with self.rig_with(tmp_path, InventoryTarget(D("25"), D("10"))) as rig:
            rig.bot.arm()
            rig.books()  # buying on NonKYC takes it even further from 25 %
            await rig.until(lambda: rig.bot.last_skip is not None)
            assert rig.bot.last_skip.reason == "inventory_band"
            assert "inventory_band" in rig.bot.status_text()
            assert "nk: XBT" in rig.bot.status_text()
            assert rig.orders.doubles == {}

    async def test_no_target_fires(self, tmp_path):
        async with self.rig_with(tmp_path, None) as rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: len(rig.orders.doubles) == 1)


NKYC = FeeToken("NKYC", D("1"), D("25"))
THIN = replace(BOT, max_slippage_pct=D("0"), min_net_pct=D("0.33"))
THIN_BOOKS = {"nk": ("0.00690000", "0.00700000"), "nx": ("0.00705000", "0.00730000")}


@pytest.mark.spec("RF-19", "RF-24")
class TestFeeWithToken:
    """Net of the thin cross: 0.31 % with the full fee, 0.36 % with NKYC (D-63)."""

    async def test_with_token_fires(self, tmp_path):
        tokens = {"nk": NKYC}
        nkyc = {"NKYC": Balance("NKYC", D("4.5"))}
        async with BotRig(tmp_path, limits=THIN, fee_tokens=tokens, nk_extra=nkyc) as rig:
            assert "NonKYC fee taker -25 % mientras haya 1 NKYC" in rig.bot.summary
            rig.bot.arm()
            rig.books(**THIN_BOOKS)
            await rig.until(lambda: rig.orders.doubles)
            assert "NonKYC fee con NKYC" in rig.bot.status_text()

    async def test_below_minimum_full_fee(self, tmp_path):
        tokens = {"nk": NKYC}
        nkyc = {"NKYC": Balance("NKYC", D("0.5"), D("9"))}  # locked does not count
        async with BotRig(tmp_path, limits=THIN, fee_tokens=tokens, nk_extra=nkyc) as rig:
            rig.bot.arm()
            rig.books(**THIN_BOOKS)
            await asyncio.sleep(0.1)
            assert rig.orders.doubles == {}
            assert rig.bot.last_skip.reason == "below_threshold"
            assert "NonKYC fee completa: 0.5 NKYC < 1" in rig.bot.status_text()

    async def test_no_configured_token_full_fee(self, tmp_path):
        nkyc = {"NKYC": Balance("NKYC", D("4.5"))}
        async with BotRig(tmp_path, limits=THIN, nk_extra=nkyc) as rig:
            rig.bot.arm()
            rig.books(**THIN_BOOKS)
            await asyncio.sleep(0.1)
            assert rig.orders.doubles == {}
            assert "NKYC" not in rig.bot.status_text() + rig.bot.summary


@pytest.mark.spec("RF-19", "RF-23", "CA-08")
class TestSharpMove:
    """D-71: the bot measures each venue's mid in a window and does not fire after a jump
    above `max_move_pct`; the move goes in `bot_fire`."""

    async def test_after_jump_waits_for_it_to_leave_window(self, tmp_path):
        limits = replace(BOT, max_move_pct=D("1"), move_window_ms=2000)
        async with BotRig(tmp_path, limits=limits) as rig:
            rig.books(nx=("0.00690000", "0.00705000"))  # no cross, mid 0.006975
            await rig.until(lambda: rig.hub.state["nx"].book.mid == D("0.006975"))
            rig.bot.arm()
            rig.books()  # Neoxa jumps to mid 0.00725 (+3.9 %): crosses, but sharply
            await rig.until(lambda: rig.bot.last_skip and rig.bot.last_skip.reason == "volatile")
            assert rig.bot.last_skip.detail.startswith("nx: mid 3.94 %")
            assert not rig.orders.doubles
            rig.clock.now += 2_001  # the jump leaves the window
            rig.books()
            await rig.until(lambda: rig.orders.doubles)
            (fire,) = rig.events("bot_fire")
            assert fire["data"]["move"] == {"nk": "0", "nx": "0"}

    async def test_jump_after_quiet_book_counts(self, tmp_path):
        """D-72: if the book has been quiet longer than the window, the mid ruling when the
        window began still counts (d-60003f fired with move 0)."""
        limits = replace(BOT, max_move_pct=D("1"), move_window_ms=2000)
        async with BotRig(tmp_path, limits=limits) as rig:
            rig.books(nx=("0.00690000", "0.00705000"))  # no cross, mid 0.006975
            await rig.until(lambda: rig.hub.state["nx"].book.mid == D("0.006975"))
            rig.bot.arm()
            rig.clock.now += 5_000  # the book stays quiet longer than the window
            rig.books()  # jumps to mid 0.00725 (+3.9 %) in a single change
            await rig.until(lambda: rig.bot.last_skip and rig.bot.last_skip.reason == "volatile")
            assert rig.bot.last_skip.detail.startswith("nx: mid 3.94 %")
            assert not rig.orders.doubles

    async def test_without_filter_fires_and_notes_move(self, rig):
        rig.books(nx=("0.00690000", "0.00705000"))
        await rig.until(lambda: rig.hub.state["nx"].book.mid == D("0.006975"))
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (fire,) = rig.events("bot_fire")
        assert D(fire["data"]["move"]["nx"]) > D("3.9")


LEAD = replace(BOT, lead_venue="nx", fill_timeout_ms=2_000)


def fills_on_place(venue, quantity):
    """The venue matches on receipt, like Neoxa: fills `qty` before the ack."""
    place = venue.place_limit

    async def placed(order_request):
        order = await place(order_request)
        if quantity > 0:
            order = await venue.simulate_fill(order.order_id, quantity)
        return order

    venue.place_limit = placed


@pytest.mark.spec("RF-07", "RF-20", "RF-21", "RF-23", "CA-08")
class TestFirstLeg:
    """With `lead_venue` (D-75), that venue's leg goes first and the other only for what
    it filled. Rig route: buy on NonKYC, sell on Neoxa."""

    async def fire(self, tmp_path, nx_fill, limits=LEAD, setup=None):
        rig = BotRig(tmp_path, limits=limits)
        fills_on_place(rig.venues["nx"], nx_fill)
        if setup is not None:
            setup(rig)
        await rig.__aenter__()
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (double,) = rig.orders.doubles.values()
        return rig, double

    async def test_fills_and_the_other_goes(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("10"))
        try:
            await rig.until(lambda: rig.orders.doubles[double.id].buy.order_id)
            sent = [(entry["venue"], D(entry["data"]["qty"])) for entry in rig.events("order_sent")]
            assert sent == [("nx", D("10")), ("nk", D("10"))]
            (fire,) = rig.events("bot_fire")
            assert fire["data"]["lead"] == "nx"
            nk = rig.orders.doubles[double.id].buy
            await rig.venues["nk"].simulate_fill(nk.order_id, D("10"))
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            assert rig.events("bot_miss") == [] and rig.events("hanging_leg") == []
        finally:
            await rig.__aexit__(None, None, None)

    async def test_partial_cancels_rest_and_other_goes_for_filled(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("4"))
        try:
            await rig.until(lambda: rig.orders.doubles[double.id].buy.order_id)
            double_state = rig.orders.doubles[double.id]
            assert double_state.buy.req.qty == D("4")
            assert double_state.sell.status == "cancelled" and double_state.sell.filled == D("4")
            assert [entry["venue"] for entry in rig.events("order_sent")] == ["nx", "nk"]
            # Neoxa's cancel goes before NonKYC's leg
            assert rig.events("cancel_sent")[0]["venue"] == "nx"
            await rig.venues["nk"].simulate_fill(double_state.buy.order_id, D("4"))
            await rig.until(lambda: rig.bot.state.status == "cooldown")
        finally:
            await rig.__aexit__(None, None, None)

    async def test_unfilled_other_stays(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("0"))
        try:
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            double_state = rig.orders.doubles[double.id]
            assert (
                double_state.sell.status == "cancelled"
                and double_state.buy.status == "skipped"
                and double_state.done
            )
            assert [entry["venue"] for entry in rig.events("order_sent")] == ["nx"]
            (miss,) = rig.events("bot_miss")
            assert miss["data"]["double"] == double.id and miss["data"]["venue"] == "nx"
            assert rig.events("hanging_leg") == []
            rig.orders.check_doubles()
            assert rig.orders.hanging == {}
            assert await rig.open_orders() == {"nk": [], "nx": []}
        finally:
            await rig.__aexit__(None, None, None)

    async def test_unfilled_is_not_a_failure(self, tmp_path):
        limits = replace(LEAD, max_consecutive_failures=1, cooldown_s=2)
        rig, _ = await self.fire(tmp_path, D("0"), limits)
        try:
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            rig.clock.now += 2_000
            rig.books()
            await rig.until(lambda: len(rig.events("bot_miss")) == 2)
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            assert rig.bot.state.failures == 0
        finally:
            await rig.__aexit__(None, None, None)

    async def test_rejected_other_stays(self, tmp_path):
        async def refuse(order_request):
            raise VenueError("Neoxa: [400] rechazada")

        rig = BotRig(tmp_path, limits=LEAD)
        rig.venues["nx"].place_limit = refuse
        async with rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "halted")
            (double,) = rig.orders.doubles.values()
            assert double.buy.status == "skipped" and double.buy.order_id is None
            assert [entry["venue"] for entry in rig.events("order_sent")] == ["nx"]
            assert rig.bot.state.reason.startswith("rejected")

    async def test_cancel_fails_because_already_filled(self, tmp_path):
        """As on 2026-10-27 17:23: "Cannot cancel order with status: filled"."""

        def setup(rig):
            nx = rig.venues["nx"]

            async def too_late(order_id):
                await nx.simulate_fill(order_id, D("10"))
                raise VenueError("Neoxa: HTTP 400: Cannot cancel order with status: filled")

            nx.cancel = too_late

        rig, double = await self.fire(tmp_path, D("0"), setup=setup)
        try:
            await rig.until(lambda: rig.orders.doubles[double.id].buy.order_id)
            double_state = rig.orders.doubles[double.id]
            assert double_state.sell.status == "filled" and double_state.buy.req.qty == D("10")
            assert rig.events("bot_miss") == []
        finally:
            await rig.__aexit__(None, None, None)

    async def test_filled_below_lot_is_hanging(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("0.00005"))  # NonKYC lot: 0.0001
        try:
            await rig.until(lambda: rig.bot.state.status == "halted")
            double_state = rig.orders.doubles[double.id]
            assert double_state.buy.status == "skipped" and double_state.sell.filled == D("0.00005")
            assert rig.bot.state.reason.startswith("hanging_leg")
            assert [entry["venue"] for entry in rig.events("order_sent")] == ["nx"]
        finally:
            await rig.__aexit__(None, None, None)
