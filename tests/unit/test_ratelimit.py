"""Neoxa limits with the 20 % margin: 480/60 s general, 8 orders and 16 cancels per
10 s. Fake clock: the test advances time and calls dispatch(), which is what the
limiter's timer does in production.
"""

import asyncio

import pytest

from xbt_terminal.core.ratelimit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def neoxa(clock):
    lim = RateLimiter(general=(480, 60), order=(8, 10), cancel=(16, 10), clock=clock)
    yield lim
    lim.close()


async def burst(lim: RateLimiter, kind: str, count: int) -> None:
    for _ in range(count):
        await asyncio.wait_for(lim.acquire(kind), 0.1)


async def pending(lim: RateLimiter, kind: str) -> asyncio.Task:
    task = asyncio.create_task(lim.acquire(kind))
    await asyncio.sleep(0)
    return task


async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.spec("RNF-03")
class TestWindows:
    async def test_the_481st_waits_a_minute(self, neoxa, clock):
        await burst(neoxa, "market", 480)
        task = await pending(neoxa, "market")
        clock.t += 59.9
        neoxa.dispatch()
        await settle()
        assert not task.done()
        clock.t += 0.2
        neoxa.dispatch()
        await settle()
        assert task.done()

    async def test_sliding_window(self, neoxa, clock):
        await burst(neoxa, "market", 240)
        clock.t += 30
        await burst(neoxa, "market", 240)
        task = await pending(neoxa, "market")
        clock.t += 30.1  # only the first 240 expire
        neoxa.dispatch()
        await settle()
        assert task.done()
        await burst(neoxa, "market", 239)
        assert not (await pending(neoxa, "market")).done()

    async def test_ninth_order_waits(self, neoxa, clock):
        await burst(neoxa, "order", 8)
        task = await pending(neoxa, "order")
        clock.t += 10.1
        neoxa.dispatch()
        await settle()
        assert task.done()

    async def test_cancels_16_per_10s(self, neoxa):
        await burst(neoxa, "cancel", 16)
        assert not (await pending(neoxa, "cancel")).done()

    async def test_orders_and_polling_share_budget(self, neoxa):
        await burst(neoxa, "order", 8)
        await burst(neoxa, "private", 472)
        assert not (await pending(neoxa, "market")).done()

    async def test_blocked_order_does_not_hold_polling(self, neoxa):
        await burst(neoxa, "order", 8)
        order = await pending(neoxa, "order")
        await burst(neoxa, "market", 1)  # passes even with an order queued
        assert not order.done()

    async def test_venue_without_order_windows(self, clock):
        lim = RateLimiter(general=(240, 60), clock=clock)
        await burst(lim, "order", 240)
        assert not (await pending(lim, "cancel")).done()
        lim.close()


@pytest.mark.spec("RNF-03")
class TestPriority:
    async def test_cancel_overtakes_polling(self, neoxa, clock):
        await burst(neoxa, "market", 2)
        clock.t += 1
        await burst(neoxa, "market", 478)
        market = await pending(neoxa, "market")
        private = await pending(neoxa, "private")
        order = await pending(neoxa, "order")
        cancel = await pending(neoxa, "cancel")

        clock.t += 59.5  # only the first 2 expire: 2 free slots
        neoxa.dispatch()
        await settle()
        assert cancel.done() and order.done()
        assert not private.done() and not market.done()

        clock.t += 1  # the rest expires
        neoxa.dispatch()
        await settle()
        assert private.done() and market.done()

    async def test_fifo_within_same_priority(self, neoxa, clock):
        await burst(neoxa, "market", 480)
        first = await pending(neoxa, "market")
        second = await pending(neoxa, "market")
        neoxa._windows["general"].stamps.popleft()  # frees exactly one slot
        neoxa.dispatch()
        await settle()
        assert first.done() and not second.done()

    async def test_cancelled_wait_consumes_no_slot(self, neoxa, clock):
        await burst(neoxa, "market", 480)
        gone = await pending(neoxa, "cancel")
        gone.cancel()
        later = await pending(neoxa, "market")
        neoxa._windows["general"].stamps.popleft()
        neoxa.dispatch()
        await settle()
        assert later.done()


async def test_unknown_type(neoxa):
    with pytest.raises(ValueError):
        await neoxa.acquire("withdraw")


async def test_real_timer_wakes_queue():
    lim = RateLimiter(general=(2, 0.05))
    await burst(lim, "market", 2)
    await asyncio.wait_for(lim.acquire("market"), 1)  # no manual dispatch()
    lim.close()
