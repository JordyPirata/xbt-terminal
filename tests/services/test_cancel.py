"""Cancelling from OrderService (T-19, RF-08, CA-05): one order, a venue's or all, with
a plan to confirm and `cancel_sent`/`cancel_ack` in the journal."""

from dataclasses import replace

import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.core.commands import CancelAll, CancelOrder, CancelVenue
from xbt_terminal.core.models import D, OrderRequest
from xbt_terminal.services.orders import CancelPlan, CancelRefused

from .test_double_orders import Setup


async def place(setup, venue, side="buy", price="0.00725000", qty="0.01", cid="t-x"):
    return await setup.venues[venue].place_limit(OrderRequest(venue, side, D(qty), D(price), cid))


async def with_orders(tmp_path):
    setup = Setup(tmp_path, nk_btc="0.005", nx_btcb2="0.5")
    order_a = await place(setup, "nk", cid="t-a")
    order_b = await place(setup, "nk", price="0.00724000", cid="t-b")
    order_c = await place(setup, "nx", side="sell", price="0.00730000", cid="t-c")
    return setup, (order_a, order_b, order_c)


@pytest.mark.spec("RF-08")
class TestPlan:
    async def test_one_order_by_id(self, tmp_path):
        setup, (order_a, _, _) = await with_orders(tmp_path)
        plan = await setup.orders.plan_cancel(CancelOrder(order_a.order_id))
        assert isinstance(plan, CancelPlan) and plan.orders == {"nk": (order_a,)}
        assert "cancelar 1 orden" in plan.summary and order_a.order_id in plan.summary

    async def test_unknown_id(self, tmp_path):
        setup, _ = await with_orders(tmp_path)
        refused = await setup.orders.plan_cancel(CancelOrder("nada"))
        assert (
            isinstance(refused, CancelRefused)
            and "no hay ninguna orden abierta nada" in refused.message
        )

    async def test_same_id_on_both_venues_does_not_guess(self, tmp_path):
        setup, (order_a, _, order_c) = await with_orders(tmp_path)
        clash = replace(order_c, order_id=order_a.order_id)

        async def both():
            return [clash]

        setup.venues["nx"].open_orders = both
        refused = await setup.orders.plan_cancel(CancelOrder(order_a.order_id))
        assert isinstance(refused, CancelRefused) and "los dos venues" in refused.message

    async def test_venue_orders(self, tmp_path):
        setup, (order_a, order_b, _) = await with_orders(tmp_path)
        plan = await setup.orders.plan_cancel(CancelVenue("nk"))
        assert set(plan.orders) == {"nk"} and set(plan.orders["nk"]) == {order_a, order_b}
        assert "2 órdenes en NonKYC" in plan.summary

    async def test_all_orders(self, tmp_path):
        setup, _ = await with_orders(tmp_path)
        plan = await setup.orders.plan_cancel(CancelAll())
        assert sum(len(venue_orders) for venue_orders in plan.orders.values()) == 3
        assert "3 órdenes" in plan.summary

    async def test_cancel_all_warns_of_foreign_orders(self, tmp_path):
        """The venue's cancel_all cancels all of the pair, not only the terminal's."""
        setup, _ = await with_orders(tmp_path)
        plan = await setup.orders.plan_cancel(CancelAll())
        assert "también las que no se enviaron desde la terminal" in plan.summary

    async def test_nothing_to_cancel(self, tmp_path):
        setup = Setup(tmp_path)
        refused = await setup.orders.plan_cancel(CancelAll())
        assert isinstance(refused, CancelRefused) and "no hay órdenes abiertas" in refused.message

    async def test_venue_without_keys(self, tmp_path):
        setup, _ = await with_orders(tmp_path)
        del setup.orders._venues["nx"]
        refused = await setup.orders.plan_cancel(CancelVenue("nx"))
        assert isinstance(refused, CancelRefused) and "sin keys" in refused.message


@pytest.mark.spec("RF-08", "RF-11", "CA-05")
class TestExecute:
    async def test_one_order(self, tmp_path):
        setup, (order_a, order_b, _) = await with_orders(tmp_path)
        result = await setup.orders.execute_cancel(
            await setup.orders.plan_cancel(CancelOrder(order_a.order_id))
        )
        assert result.ok and result.remaining == {"nk": 0}
        assert [order.order_id for order in await setup.venues["nk"].open_orders()] == [
            order_b.order_id
        ]
        sent, ack = setup.lines()
        assert (sent["event"], ack["event"]) == ("cancel_sent", "cancel_ack")
        assert sent["data"] == {"scope": "order", "order_ids": [order_a.order_id]}
        assert sent["client_id"] == "t-a" and ack["data"]["remaining"] == 0

    async def test_cancel_all_leaves_zero_open(self, tmp_path):
        """CA-05 with two simulated venues."""
        setup, _ = await with_orders(tmp_path)
        result = await setup.orders.execute_cancel(await setup.orders.plan_cancel(CancelAll()))
        assert result.ok and result.remaining == {"nk": 0, "nx": 0}
        for venue in setup.venues.values():
            assert await venue.open_orders() == []
        assert (
            sorted(event for event, _ in setup.events()) == ["cancel_ack"] * 2 + ["cancel_sent"] * 2
        )
        assert "quedan 0" in result.message

    async def test_one_venue_fails_other_cancels(self, tmp_path):
        setup, _ = await with_orders(tmp_path)

        async def boom():
            raise VenueError("Neoxa: HTTP 429")

        setup.venues["nx"].cancel_all = boom
        result = await setup.orders.execute_cancel(await setup.orders.plan_cancel(CancelAll()))
        assert not result.ok and "Neoxa: HTTP 429" in result.message
        assert await setup.venues["nk"].open_orders() == []
        assert ("error", "nx") in setup.events() and ("cancel_ack", "nk") in setup.events()

    async def test_remaining_ones_are_reported(self, tmp_path):
        setup, _ = await with_orders(tmp_path)

        async def noop():
            return None  # the venue answers ok but does not cancel

        setup.venues["nx"].cancel_all = noop
        result = await setup.orders.execute_cancel(await setup.orders.plan_cancel(CancelAll()))
        assert not result.ok and result.remaining["nx"] == 1
        assert "Neoxa: queda 1 abierta" in result.message
