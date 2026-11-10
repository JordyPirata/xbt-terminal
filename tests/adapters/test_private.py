"""Common tracking of own orders and fills (D-14): diff between refreshes and a polling
loop with its private connection state."""

import asyncio
from contextlib import aclosing
from dataclasses import replace

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.adapters.private import (
    OrderTracker,
    PrivateSource,
    fan_in,
    poll_private,
    refresh,
)
from xbt_terminal.core.models import ConnState, D, FeeUpdate, Fill, Order, PastFill

T0 = 1_792_497_600_000


def order(oid="o-1", filled="0", status="open", cid=None, qty="0.01"):
    return Order("nx", oid, cid, "buy", D("0.0072"), D(qty), D(filled), status, T0)


def fill(tid="f-1", oid="o-1", qty="0.004", ts=T0 + 1):
    return Fill("nx", tid, oid, "buy", D("0.0072"), D(qty), D("0"), "BTC", ts)


@pytest.mark.spec("RF-08", "RF-09")
class TestTracker:
    def test_first_refresh_gives_orders_and_old_fills_as_past(self):
        """D-68: trades already there are not new fills, but the account compares them with
        the journal."""
        tracker = OrderTracker()
        events, gone = tracker.update(
            [order("a"), order("b")], [fill("f-2", ts=T0 + 2), fill("f-1")]
        )
        past = [PastFill(fill("f-1")), PastFill(fill("f-2", ts=T0 + 2))]  # by time
        assert events == [order("a"), order("b"), *past]
        assert gone == [] and tracker.open == [order("a"), order("b")]

    def test_past_only_in_first_refresh(self):
        tracker = OrderTracker()
        tracker.update([], [fill("f-1")])
        assert tracker.update([], [fill("f-1")])[0] == []
        assert tracker.update([], [fill("f-1"), fill("f-2")])[0] == [fill("f-2")]

    def test_only_notifies_changes(self):
        tracker = OrderTracker()
        tracker.update([order("a"), order("b")], [])
        events, _ = tracker.update([order("a"), order("b", filled="0.004", status="partial")], [])
        assert events == [order("b", filled="0.004", status="partial")]

    def test_new_fill_once_and_in_order(self):
        tracker = OrderTracker()
        tracker.update([], [fill("f-1")])
        new = [fill("f-3", ts=T0 + 3), fill("f-2", ts=T0 + 2), fill("f-1")]
        events, _ = tracker.update([], new)
        assert events == [fill("f-2", ts=T0 + 2), fill("f-3", ts=T0 + 3)]
        assert tracker.update([], new)[0] == []

    def test_vanishing_order_queried_until_resolved(self):
        tracker = OrderTracker()
        tracker.update([order("a")], [])
        assert tracker.update([], [])[1] == ["a"]
        assert tracker.update([], [])[1] == ["a"]  # stays pending until resolved
        final = order("a", filled="0.01", status="filled")
        assert tracker.resolve(final) == [final]
        assert tracker.update([], [])[1] == [] and tracker.open == []

    def test_resolving_untracked_does_not_notify(self):
        assert OrderTracker().resolve(order("x", status="cancelled")) == []

    def test_remembers_client_id_of_what_terminal_sent(self):
        """Neoxa keeps no client_id: place_limit's is kept across refreshes."""
        tracker = OrderTracker()
        tracker.remember(order("a", cid="t-abc123"))
        events, _ = tracker.update([order("a")], [])
        assert events == [order("a", cid="t-abc123")]
        assert tracker.resolve(order("a", status="cancelled"))[0].client_id == "t-abc123"

    def test_sent_is_tracked_even_if_not_seen_active(self):
        """Seen for real (2026-10-22 04:07, D-41): a sell on NonKYC filled in ~1 s, before the
        next poll, and its close never arrived."""
        tracker = OrderTracker()
        tracker.update([], [])
        tracker.remember(order("a", cid="t-7bbce5"))
        assert tracker.open == [order("a", cid="t-7bbce5")]
        assert tracker.update([], [])[1] == ["a"]  # no longer active: queried
        final = order("a", filled="0.01", status="filled")
        assert tracker.resolve(final) == [
            order("a", filled="0.01", status="filled", cid="t-7bbce5")
        ]

    def test_sent_already_finished_is_not_tracked(self):
        """Neoxa answers "filled" on creating: nothing to query later."""
        tracker = OrderTracker()
        tracker.remember(order("a", filled="0.01", status="filled", cid="t-1"))
        assert tracker.open == [] and tracker.update([], [])[1] == []

    def test_sent_still_active_is_not_deemed_gone(self):
        tracker = OrderTracker()
        tracker.remember(order("a", cid="t-1"))
        events, gone = tracker.update([order("a")], [])
        assert gone == [] and tracker.open == [order("a", cid="t-1")]
        assert events == [order("a", cid="t-1")]  # reported anyway the first time


class FakeSource:
    def __init__(self):
        self.active: list[Order] = []
        self.fills: list[Fill] = []
        self.final: dict[str, Order] = {}
        self.fail: Exception | None = None
        self.calls = 0

    async def fetch_active(self):
        self.calls += 1
        if self.fail:
            raise self.fail
        return list(self.active)

    async def fetch_fills(self):
        return list(self.fills)

    async def fetch_final(self, oid):
        return self.final.get(oid)

    def hooks(self):
        return PrivateSource(self.fetch_active, self.fetch_fills, self.fetch_final)


def runner(src, wake, every=0.01, source="rest"):
    def start(emit):
        return poll_private(
            "nx",
            OrderTracker(),
            src.hooks(),
            emit,
            every_s=lambda: every,
            source=lambda: source,
            wake=wake,
            clock=lambda: T0,
        )

    return start


async def take(stream, count, timeout=2.0):
    out = []
    async with asyncio.timeout(timeout):
        async for ev in stream:
            out.append(ev)
            if len(out) == count:
                return out
    return out


@pytest.mark.spec("RF-09", "RF-14")
class TestPollingLoop:
    async def test_up_state_and_orders(self):
        src = FakeSource()
        src.active = [order("a")]
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            state, event = await take(stream, 2)
        assert isinstance(state, ConnState)
        assert (state.channel, state.source, state.status) == ("private", "rest", "up")
        assert event == order("a")

    async def test_network_failure_marks_down_and_recovers(self):
        src = FakeSource()
        src.fail = httpx.ConnectError("sin red")
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            down = (await take(stream, 1))[0]
            src.fail = None
            up = (await take(stream, 1))[0]
        assert (down.status, up.status) == ("down", "up")

    async def test_venue_error_is_also_down(self):
        src = FakeSource()
        src.fail = VenueError("401 Authorisation failed")
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            assert (await take(stream, 1))[0].status == "down"

    async def test_finished_order_resolves(self):
        src = FakeSource()
        src.active = [order("a")]
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            await take(stream, 2)
            src.active = []
            src.final["a"] = order("a", filled="0.01", status="filled")
            assert await take(stream, 1) == [order("a", filled="0.01", status="filled")]

    async def test_wake_refreshes_without_waiting_for_the_rate(self):
        src = FakeSource()
        wake = asyncio.Event()
        async with aclosing(fan_in(runner(src, wake, every=60))) as stream:
            await take(stream, 1)
            src.active = [order("a")]
            wake.set()
            assert await take(stream, 1, timeout=1.0) == [order("a")]

    async def test_source_change_reemits_state(self):
        src = FakeSource()
        wake = asyncio.Event()
        where = {"source": "rest"}

        def start(emit):
            return poll_private(
                "nk",
                OrderTracker(),
                src.hooks(),
                emit,
                every_s=lambda: 60,
                source=lambda: where["source"],
                wake=wake,
                clock=lambda: T0,
            )

        async with aclosing(fan_in(start)) as stream:
            assert (await take(stream, 1))[0].source == "rest"
            where["source"] = "ws"
            wake.set()
            assert (await take(stream, 1))[0].source == "ws"

    async def test_bug_is_not_swallowed(self):
        src = FakeSource()
        src.fail = ZeroDivisionError("bug")
        with pytest.raises(ZeroDivisionError):
            async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
                await take(stream, 5)


@pytest.mark.spec("RF-09")
async def test_fan_in_mixes_workers_and_stops_them_on_close():
    stopped = []

    def worker(name):
        async def run(emit):
            try:
                for i in range(3):
                    emit((name, i))
                    await asyncio.sleep(0)
                await asyncio.Event().wait()
            finally:
                stopped.append(name)

        return run

    async with aclosing(fan_in(worker("a"), worker("b"))) as stream:
        got = await take(stream, 6)
    assert sorted(got) == [(worker_name, i) for worker_name in "ab" for i in range(3)]
    await asyncio.sleep(0)
    assert sorted(stopped) == ["a", "b"]


@pytest.mark.spec("RF-07", "RF-09")
async def test_order_filled_before_first_poll_shows_filled():
    """End to end in the refresh: the venue no longer lists it as active, and `getorder`
    says it filled; a filled `Order` comes out with the client_id."""
    tracker = OrderTracker()
    filled = order("a", filled="0.01", status="filled")

    async def active():
        return []

    async def fills():
        return [fill("f-1", oid="a", qty="0.01")]

    async def final(oid):
        return filled if oid == "a" else None

    src = PrivateSource(active, fills, final)
    await refresh(tracker, src)  # first refresh: seeds
    tracker.remember(order("a", cid="t-7bbce5"))
    events = await refresh(tracker, src)
    assert order("a", filled="0.01", status="filled", cid="t-7bbce5") in events
    assert tracker.open == []


@pytest.mark.spec("RF-11", "RF-25")
class TestCorrectedFee:
    """NonKYC redoes the fee of an already published trade (BTC → NKYC, D-64)."""

    def test_same_fee_does_not_notify(self):
        tracker = OrderTracker()
        tracker.update([], [])
        tracker.update([], [fill("f-1")])
        assert tracker.update([], [fill("f-1")])[0] == []

    def test_another_fee_notifies_once_without_another_fill(self):
        tracker = OrderTracker()
        tracker.update([], [])
        assert tracker.update([], [fill("f-1")])[0] == [fill("f-1")]
        fixed = replace(fill("f-1"), fee=D("0.0000893166"), fee_asset="NKYC")
        events, _ = tracker.update([], [fixed])
        assert events == [FeeUpdate("nx", "f-1", "o-1", D("0.0000893166"), "NKYC", T0 + 1)]
        assert tracker.update([], [fixed])[0] == []

    def test_also_if_trade_was_history(self):
        tracker = OrderTracker()
        tracker.update([], [fill("f-viejo")])
        fixed = replace(fill("f-viejo"), fee=D("0.00001"))
        (event,), _ = tracker.update([], [fixed])
        assert isinstance(event, FeeUpdate) and event.fee == D("0.00001")
