"""Uncertain sends and reconciliation in OrderService (T-25, RNF-12, D-26).

The venue is a DryRunVenue with NonKYC 0.00720000 / 0.00730000 (see test_orders).
"Lost response" = the order is created on the venue but `place_limit` raises
`UncertainError`, like a timeout after the POST."""

import pytest

from xbt_terminal.adapters.base import UncertainError, VenueError
from xbt_terminal.core.commands import DoubleOrder
from xbt_terminal.core.models import D, OrderRequest
from xbt_terminal.services.orders import (
    DoubleSent,
    OrderService,
    Rejected,
    Sent,
    Uncertain,
)

from . import test_double_orders as double
from .test_orders import LIMITS, NOW, Setup, buy, sell


class Rig(Setup):
    """test_orders Setup with a clock that can move."""

    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, **kw)
        self.now = NOW
        ids = iter(f"t-{i:06d}" for i in range(1, 100))
        self.orders = OrderService(
            self.hub,
            {"nk": self.venue},
            self.journal,
            LIMITS,
            clock=lambda: self.now,
            new_client_id=lambda: next(ids),
        )

    def lose_reply(self, *, create=True):
        """The next send loses its response; with `create`, the order is created."""
        real = self.venue.place_limit

        async def lost(order_request):
            self.venue.place_limit = real
            if create:
                await real(order_request)
            raise UncertainError("NonKYC: sin respuesta (ReadTimeout)")

        self.venue.place_limit = lost

    async def send(self, command):
        return await self.orders.confirm(await self.orders.prepare(command))

    def events(self):
        return [ln["event"] for ln in self.lines()]


@pytest.mark.spec("RNF-12", "RF-11")
class TestUncertainSend:
    async def test_not_a_failure(self, tmp_path):
        rig = Rig(tmp_path)
        rig.lose_reply()
        result = await rig.send(buy())
        assert isinstance(result, Uncertain) and "ReadTimeout" in result.error
        assert rig.events() == ["order_sent", "order_unknown"]
        assert rig.lines()[1]["client_id"] == "t-000001"
        assert rig.lines()[1]["data"] == {"error": "NonKYC: sin respuesta (ReadTimeout)"}
        assert list(rig.orders.uncertain) == ["t-000001"]

    async def test_clear_rejection_stays_a_failure(self, tmp_path):
        rig = Rig(tmp_path)

        async def boom(order_request):
            raise VenueError("Insufficient funds")

        rig.venue.place_limit = boom
        await rig.send(buy())
        assert rig.events() == ["order_sent", "order_error"]
        assert rig.orders.uncertain == {}


@pytest.mark.spec("RNF-12", "RF-11")
class TestReconcile:
    async def test_shows_up_by_client_id(self, tmp_path):
        rig = Rig(tmp_path)
        rig.lose_reply()
        await rig.send(buy())
        (reconciled,) = await rig.orders.reconcile()
        assert (reconciled.client_id, reconciled.outcome) == ("t-000001", "found")
        assert "dry-nk-1" in reconciled.message
        ack = rig.lines()[-1]
        assert (ack["event"], ack["client_id"]) == ("order_ack", "t-000001")
        assert ack["data"] == {"order_id": "dry-nk-1", "status": "open", "reconciled": True}
        assert rig.orders.uncertain == {}

    async def test_reconciled_counts_for_validating(self, tmp_path):
        """Once found, a sell crossing it is rejected (self_cross)."""
        rig = Rig(tmp_path)
        rig.lose_reply()
        await rig.send(buy(price="0.00725000"))
        await rig.orders.reconcile()
        result = await rig.orders.prepare(sell(price="0.00724000"))
        assert isinstance(result, Rejected) and "self_cross" in result.codes

    async def test_no_trace_stays_uncertain_until_60_s(self, tmp_path):
        rig = Rig(tmp_path)
        rig.lose_reply(create=False)
        await rig.send(buy())
        rig.now = NOW + 59_000
        assert await rig.orders.reconcile() == []
        assert rig.events() == ["order_sent", "order_unknown"]
        rig.now = NOW + 61_000
        (reconciled,) = await rig.orders.reconcile()
        assert reconciled.outcome == "missing"
        err = rig.lines()[-1]
        assert (err["event"], err["client_id"]) == ("order_error", "t-000001")
        assert err["data"]["reconciled"] is True and "no existe" in err["data"]["error"]
        assert rig.orders.uncertain == {}

    async def test_unresponsive_venue_is_retried(self, tmp_path):
        rig = Rig(tmp_path)
        rig.lose_reply()
        await rig.send(buy())

        async def down(since):
            raise VenueError("caído")

        real, rig.venue.recent_orders = rig.venue.recent_orders, down
        # not even past the deadline is it deemed nonexistent without being able to look
        rig.now = NOW + 120_000
        assert await rig.orders.reconcile() == []
        assert list(rig.orders.uncertain) == ["t-000001"]
        rig.venue.recent_orders = real
        (reconciled,) = await rig.orders.reconcile()
        assert reconciled.outcome == "found"


@pytest.mark.spec("RNF-12")
class TestWithoutClientId:
    """Neoxa (PA-09): by side, price and quantity, without already known orders."""

    async def test_equal_known_one_not_confused(self, tmp_path):
        rig = Rig(tmp_path)
        rig.venue.keeps_client_id = False
        first = await rig.send(buy())
        assert isinstance(first, Sent)
        rig.lose_reply()
        await rig.send(buy())
        (reconciled,) = await rig.orders.reconcile()
        assert reconciled.outcome == "found" and "dry-nk-2" in reconciled.message

    async def test_equal_one_sent_later_by_terminal_not_confused(self, tmp_path):
        rig = Rig(tmp_path)
        rig.venue.keeps_client_id = False
        rig.lose_reply()
        await rig.send(buy())
        assert isinstance(await rig.send(buy()), Sent)  # dry-nk-2, with its ack
        (reconciled,) = await rig.orders.reconcile()
        assert reconciled.outcome == "found" and "dry-nk-1" in reconciled.message

    async def test_two_candidates_stays_uncertain_and_warns_once(self, tmp_path):
        rig = Rig(tmp_path)
        rig.venue.keeps_client_id = False
        rig.lose_reply()
        await rig.send(buy())
        # another identical, created outside the terminal in the same window
        await rig.venue.place_limit(OrderRequest("nk", "buy", D("0.01"), D("0.00725"), "x"))
        (reconciled,) = await rig.orders.reconcile()
        assert reconciled.outcome == "ambiguous" and "2" in reconciled.message
        assert await rig.orders.reconcile() == []
        assert list(rig.orders.uncertain) == ["t-000001"]
        assert rig.events() == ["order_sent", "order_unknown"]


@pytest.mark.spec("RNF-12", "RF-07")
class TestDoubleOrder:
    async def test_uncertain_leg_stays_unknown_and_resolves(self, tmp_path):
        rig = double.Setup(tmp_path)
        real = rig.venues["nx"].place_limit

        async def lost(order_request):
            await real(order_request)
            raise UncertainError("Neoxa: sin respuesta (ReadTimeout)")

        rig.venues["nx"].place_limit = lost
        command = DoubleOrder(D("0.01"), "nk", D("0.00725000"), "nx", D("0.00726000"))
        result = await rig.orders.confirm_double(await rig.orders.prepare_double(command))
        assert isinstance(result, DoubleSent)
        assert (result.state.buy.status, result.state.sell.status) == ("open", "unknown")
        assert "ReadTimeout" in result.state.sell.error
        unknown = next(ln for ln in rig.lines() if ln["event"] == "order_unknown")
        assert unknown["data"]["double"] == "d-000001"
        (reconciled,) = await rig.orders.reconcile()
        assert reconciled.outcome == "found"
        order_leg = rig.orders.doubles["d-000001"].sell
        assert (order_leg.status, order_leg.order_id, order_leg.error) == ("open", "dry-nx-1", None)
        ack = rig.lines()[-1]
        assert ack["event"] == "order_ack" and ack["data"]["double"] == "d-000001"

    async def test_missing_leg_is_rejected(self, tmp_path):
        rig = double.Setup(tmp_path)

        async def lost(order_request):
            raise UncertainError("Neoxa: sin respuesta (ReadTimeout)")

        rig.venues["nx"].place_limit = lost
        command = DoubleOrder(D("0.01"), "nk", D("0.00725000"), "nx", D("0.00726000"))
        await rig.orders.confirm_double(await rig.orders.prepare_double(command))
        rig.now = NOW + 61_000
        (reconciled,) = await rig.orders.reconcile()
        assert reconciled.outcome == "missing"
        assert rig.orders.doubles["d-000001"].sell.status == "rejected"
