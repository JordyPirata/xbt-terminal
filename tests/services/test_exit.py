"""Safe exit in OrderService (T-28, RNF-15, D-29): what already left for the venue
finishes and lands in the journal even if its waiter is cancelled."""

import asyncio

import pytest

from xbt_terminal.core.commands import CancelVenue

from .test_orders import Setup, buy


def slow(venue, method, delay=0.05):
    real = getattr(venue, method)

    async def held(*args):
        await asyncio.sleep(delay)
        return await real(*args)

    setattr(venue, method, held)


def events(setup):
    return [ln["event"] for ln in setup.lines()]


@pytest.mark.spec("RNF-15", "RF-11")
class TestInFlight:
    async def test_send_cancelled_midway_still_finishes(self, tmp_path):
        setup = Setup(tmp_path)
        pending = await setup.orders.prepare(buy())
        slow(setup.venue, "place_limit")
        task = asyncio.create_task(setup.orders.confirm(pending))
        while events(setup) != ["order_sent"]:
            await asyncio.sleep(0.001)
        assert setup.orders.in_flight == 1
        task.cancel()  # as when closing the app with a send in flight
        await asyncio.gather(task, return_exceptions=True)
        await setup.orders.drain()
        assert events(setup) == ["order_sent", "order_ack"]
        assert [order.order_id for order in await setup.venue.open_orders()] == ["dry-nk-1"]
        assert setup.orders.in_flight == 0

    async def test_cancel_midway_still_finishes(self, tmp_path):
        setup = Setup(tmp_path)
        await setup.orders.confirm(await setup.orders.prepare(buy()))
        plan = await setup.orders.plan_cancel(CancelVenue("nk"))
        slow(setup.venue, "cancel_all")
        task = asyncio.create_task(setup.orders.execute_cancel(plan))
        while "cancel_sent" not in events(setup):
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await setup.orders.drain()
        assert events(setup)[-2:] == ["cancel_sent", "cancel_ack"]
        assert await setup.venue.open_orders() == []

    async def test_nothing_in_flight_drain_returns_at_once(self, tmp_path):
        setup = Setup(tmp_path)
        await asyncio.wait_for(setup.orders.drain(), 0.1)
