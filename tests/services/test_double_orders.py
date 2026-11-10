"""Double order in OrderService (T-18, design.md §6.4) with two DryRunVenues.
Books: NonKYC 0.00720000 / 0.00730000, Neoxa 0.00722000 / 0.00732000."""

import json

import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.core.commands import DoubleOrder
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, BookSnapshot, D, Level
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import (
    DoubleRejected,
    DoubleSent,
    OrderService,
    PendingDouble,
)

from ..fakes import T0, FakeFeed, spec
from .test_orders import LIMITS

NOW = T0 + 60_000


class Setup:
    def __init__(self, tmp_path, nk_btc="0.005", nx_btcb2="0.5"):
        self.now = NOW
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.hub = MarketHub(feeds, clock=lambda: self.now)
        for venue_id in feeds:
            self.hub.state[venue_id].spec = spec(venue_id)
        self.book("nk", "0.00720000", "0.00730000")
        self.book("nx", "0.00722000", "0.00732000")
        self.venues = {
            "nk": DryRunVenue(feeds["nk"], self.balances(nk_btc, "0"), clock=lambda: self.now),
            "nx": DryRunVenue(feeds["nx"], self.balances("0", nx_btcb2), clock=lambda: self.now),
        }
        self.logs = tmp_path / "logs"
        self.journal = Journal(self.logs, "dry-run", Redactor([]))
        ids = iter(f"t-{i:06d}" for i in range(1, 100))
        self.orders = OrderService(
            self.hub,
            self.venues,
            self.journal,
            LIMITS,
            clock=lambda: self.now,
            new_client_id=lambda: next(ids),
            new_double_id=lambda: "d-000001",
        )

    @staticmethod
    def balances(btc, btcb2):
        return {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D(btcb2))}

    def book(self, venue, bid, ask):
        snap = BookSnapshot(venue, (Level(D(bid), D("1")),), (Level(D(ask), D("1")),), self.now)
        self.hub.apply(venue, snap)

    def lines(self):
        files = sorted(self.logs.glob("journal-*.jsonl"))
        return [json.loads(ln) for f in files for ln in f.read_text().splitlines()]

    def events(self):
        return [(ln["event"], ln["venue"]) for ln in self.lines()]

    async def feed(self, venue):
        """Pass OrderService what the simulated venue published (like AccountService)."""
        for order in await self.venues[venue].open_orders():
            self.orders.on_private_event(venue, order)


def dbl(qty="0.01", buy="0.00725000", sell="0.00726000"):
    return DoubleOrder(D(qty), "nk", D(buy), "nx", D(sell))


@pytest.mark.spec("RF-07", "RF-10")
class TestPrepare:
    async def test_two_legs_and_a_summary(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare_double(dbl())
        assert isinstance(p, PendingDouble) and p.id == "d-000001"
        assert (p.buy.req.venue, p.buy.req.side, p.sell.req.venue, p.sell.req.side) == (
            "nk", "buy", "nx", "sell",
        )  # fmt: skip
        assert p.gross == D("0.0000001")  # (0.00726 - 0.00725) × 0.01
        assert p.net == D("0.0000726") * D("0.998") - D("0.0000725") * D("1.002")
        text = p.summary
        assert "COMPRA 0.0100 XBT @ 0.00725000 en NonKYC" in text
        assert "VENTA 0.01000000 XBT @ 0.00726000 en Neoxa" in text
        assert "bruto +0.00000010 BTC (+10 sats)" in text and "neto -0.00000019 BTC" in text
        assert s.lines() == []

    async def test_rejected_leg_sends_neither(self, tmp_path):
        s = Setup(tmp_path, nx_btcb2="0")  # Neoxa without BTCB2 to sell
        r = await s.orders.prepare_double(dbl())
        assert isinstance(r, DoubleRejected)
        assert "no se envía ninguna pierna" in r.message and "venta en Neoxa" in r.message
        assert "insufficient_balance" in r.codes
        assert s.events() == [("reject_local", "nx")]
        assert s.lines()[0]["data"]["double"] == "d-000001"
        for venue in s.venues.values():
            assert await venue.open_orders() == []

    async def test_both_rejected(self, tmp_path):
        s = Setup(tmp_path, nk_btc="0", nx_btcb2="0")
        r = await s.orders.prepare_double(dbl())
        assert isinstance(r, DoubleRejected)
        assert s.events() == [("reject_local", "nk"), ("reject_local", "nx")]


@pytest.mark.spec("RF-07", "CA-03")
class TestSend:
    async def test_sends_both_and_follows_them(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        assert isinstance(sent, DoubleSent)
        state = sent.state
        assert (state.buy.status, state.sell.status) == ("open", "open")
        assert (state.buy.order_id, state.sell.order_id) == ("dry-nk-1", "dry-nx-1")
        assert sorted(s.events()) == sorted(
            [("order_sent", "nk"), ("order_sent", "nx"), ("order_ack", "nk"), ("order_ack", "nx")]
        )
        assert {ln["data"]["double"] for ln in s.lines()} == {"d-000001"}
        assert s.orders.doubles["d-000001"] == state

    async def test_confirm_revalidates_and_on_failure_neither_goes(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare_double(dbl())
        s.book("nx", "0.00800000", "0.00810000")  # Neoxa's mid moves 10 %
        r = await s.orders.confirm_double(p)
        assert isinstance(r, DoubleRejected) and "mid_deviation" in r.codes
        assert s.events() == [("reject_local", "nx")]
        assert "d-000001" not in s.orders.doubles

    async def test_error_sending_a_leg(self, tmp_path):
        s = Setup(tmp_path)

        async def boom(order_request):
            raise VenueError("Neoxa: HTTP 400: PRICE_OUT_OF_BAND")

        s.venues["nx"].place_limit = boom
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        state = sent.state
        assert (state.buy.status, state.sell.status) == ("open", "rejected")
        assert "PRICE_OUT_OF_BAND" in (state.sell.error or "")
        assert ("order_error", "nx") in s.events() and ("order_ack", "nk") in s.events()

    async def test_order_changes_update_the_leg(self, tmp_path):
        s = Setup(tmp_path)
        seen = []
        s.orders.subscribe_doubles(seen.append)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.004"))
        await s.feed("nk")
        state = s.orders.doubles["d-000001"]
        assert (state.buy.status, state.buy.filled) == ("partial", D("0.004"))
        assert "d-000001" in seen


@pytest.mark.spec("RF-07", "CA-03")
class TestHangingLeg:
    async def test_filled_and_other_open_over_n_seconds(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        order = await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        s.orders.on_private_event("nk", order)
        s.now += LIMITS.hanging_leg_after_s * 1000
        assert s.orders.check_doubles() == []  # not yet at the exact limit
        s.now += 1
        assert s.orders.check_doubles() == ["d-000001"]
        (line,) = [ln for ln in s.lines() if ln["event"] == "hanging_leg"]
        assert line["venue"] is None and line["data"]["double"] == "d-000001"
        assert "venta en Neoxa" in line["data"]["reason"]
        assert line["data"]["legs"]["buy"]["filled"] == "0.01"
        assert s.orders.hanging["d-000001"].startswith("venta en Neoxa")

    async def test_warns_only_once(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        s.orders.on_private_event(
            "nk", await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        )
        s.now += 31_000
        s.orders.check_doubles()
        s.now += 10_000
        assert s.orders.check_doubles() == []
        assert [ln["event"] for ln in s.lines()].count("hanging_leg") == 1

    async def test_rejected_on_send_with_other_filled_is_immediate(self, tmp_path):
        s = Setup(tmp_path)

        async def boom(order_request):
            raise VenueError("rechazada por el venue")

        s.venues["nx"].place_limit = boom
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        s.orders.on_private_event(
            "nk", await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        )
        assert s.orders.check_doubles() == ["d-000001"]

    async def test_both_filled_is_not_hanging(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        for venue, order_leg in (("nk", sent.state.buy), ("nx", sent.state.sell)):
            s.orders.on_private_event(
                venue, await s.venues[venue].simulate_fill(order_leg.order_id, D("0.01"))
            )
        s.now += 120_000
        assert s.orders.check_doubles() == [] and s.orders.doubles["d-000001"].done
