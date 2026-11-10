"""Atomic sends per venue (T-26, RNF-13, D-27).

Same market as test_orders: NonKYC 0.00720000 / 0.00730000. A buy at 0.00725 and a
sell at 0.00724 cross (`self_cross`): if both validate at once without counting the
other, both go out."""

import asyncio

import pytest

from xbt_terminal.core.commands import DoubleOrder
from xbt_terminal.core.models import D
from xbt_terminal.services.orders import DoubleSent, Rejected, Sent, Uncertain

from . import test_double_orders as double
from .test_orders import NOW, buy, sell
from .test_uncertain import Rig


def slow(venue, gate: asyncio.Event):
    """`place_limit` waits for `gate` before creating the order: a send in flight."""
    real = venue.place_limit

    async def held(order_request):
        await gate.wait()
        return await real(order_request)

    venue.place_limit = held


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.spec("RNF-13", "RF-10")
class TestSameVenue:
    async def test_second_counts_the_one_in_flight(self, tmp_path):
        s = Rig(tmp_path)
        first = await s.orders.prepare(buy(price="0.00725000"))
        second = await s.orders.prepare(sell(price="0.00724000"))
        gate = asyncio.Event()
        slow(s.venue, gate)
        a = asyncio.create_task(s.orders.confirm(first))
        b = asyncio.create_task(s.orders.confirm(second))
        await settle()
        assert not b.done()  # waits for the first send to end
        gate.set()
        ra, rb = await a, await b
        assert isinstance(ra, Sent)
        assert isinstance(rb, Rejected) and "self_cross" in rb.codes
        assert s.events() == ["order_sent", "order_ack", "reject_local"]

    async def test_uncertain_counts_as_open(self, tmp_path):
        """It may exist on the venue: until known, it counts (when in doubt, do not send)."""
        s = Rig(tmp_path)
        s.lose_reply(create=False)
        assert isinstance(await s.send(buy(price="0.00725000")), Uncertain)
        result = await s.orders.prepare(sell(price="0.00724000"))
        assert isinstance(result, Rejected) and "self_cross" in result.codes

    async def test_resolved_as_missing_stops_counting(self, tmp_path):
        s = Rig(tmp_path)
        s.lose_reply(create=False)
        await s.send(buy(price="0.00725000"))
        s.now = NOW + 61_000
        s.book("nk", "0.00720000", "0.00730000", ts=s.now)
        (r,) = await s.orders.reconcile()
        assert r.outcome == "missing"
        assert isinstance(await s.send(sell(price="0.00724000")), Sent)

    async def test_uncertain_and_found_does_not_count_twice(self, tmp_path):
        """With the order already on the venue and the same client_id, only once."""
        s = Rig(tmp_path)
        s.lose_reply()
        await s.send(buy(price="0.00725000"))
        open_orders = await s.orders._snapshot("nk", s.venue)
        assert [o.client_id for o in open_orders[0]] == ["t-000001"]


@pytest.mark.spec("RNF-13")
class TestDifferentVenues:
    async def test_do_not_block_each_other(self, tmp_path):
        s = double.Setup(tmp_path)
        gate = asyncio.Event()
        slow(s.venues["nk"], gate)
        stuck = asyncio.create_task(s.orders.confirm(await s.orders.prepare(buy(venue="nk"))))
        await settle()
        nx = await s.orders.prepare(sell(venue="nx", price="0.00727000"))
        result = await asyncio.wait_for(s.orders.confirm(nx), 1)
        assert isinstance(result, Sent) and not stuck.done()
        gate.set()
        assert isinstance(await stuck, Sent)

    async def test_double_order_waits_for_both_venues(self, tmp_path):
        s = double.Setup(tmp_path)
        gate = asyncio.Event()
        slow(s.venues["nx"], gate)
        stuck = asyncio.create_task(
            s.orders.confirm(await s.orders.prepare(sell(venue="nx", price="0.00727000")))
        )
        await settle()
        command = DoubleOrder(D("0.01"), "nk", D("0.00725000"), "nx", D("0.00726000"))
        pending = await s.orders.prepare_double(command)
        task = asyncio.create_task(s.orders.confirm_double(pending))
        await settle()
        assert not task.done()
        assert [e for e, _ in s.events()] == ["order_sent"]  # not even the nk leg goes
        gate.set()
        assert isinstance(await stuck, Sent)
        assert isinstance(await asyncio.wait_for(task, 1), DoubleSent)
