"""DryRunVenue (D-13): orders simulated in memory with config balances; the real venue
gives market data."""

import asyncio

import pytest

from xbt_terminal.adapters.base import Venue, VenueError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.core.models import Balance, D, Order, OrderRequest

from ..fakes import FakeFeed, spec


def balances(btc="0.005", btcb2="0.5"):
    return {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D(btcb2))}


def make(**kw):
    return DryRunVenue(FakeFeed("nk"), balances(**kw), clock=lambda: 1_000)


def order_request(side="buy", qty="0.01", price="0.00730000", cid="t-abc123"):
    return OrderRequest("nk", side, D(qty), D(price), cid)


@pytest.mark.spec("RF-13", "RNF-09")
class TestInterface:
    def test_fulfils_venue(self):
        assert isinstance(make(), Venue)
        assert make().id == "nk"

    async def test_market_delegated_to_real_venue(self):
        venue = make()
        assert (await venue.market()) == spec("nk")
        assert venue.inner.calls == ["market"]


@pytest.mark.spec("RF-13")
class TestOrders:
    async def test_buy_opens_order_and_locks_btc_with_fee(self):
        venue = make()
        order = await venue.place_limit(order_request())
        assert order == Order(
            "nk", "dry-nk-1", "t-abc123", "buy", D("0.00730000"), D("0.01"), D("0"), "open", 1_000
        )
        bal = await venue.balances()
        locked = D("0.01") * D("0.00730000") * D("1.002")  # spec's 0.2 % taker
        assert bal["BTC"].locked == locked and bal["BTC"].available == D("0.005") - locked
        assert bal["BTCB2"].available == D("0.5")
        assert await venue.open_orders() == [order]

    async def test_sell_locks_btcb2(self):
        venue = make()
        await venue.place_limit(order_request("sell", qty="0.2"))
        bal = await venue.balances()
        assert (bal["BTCB2"].available, bal["BTCB2"].locked) == (D("0.3"), D("0.2"))
        assert bal["BTC"].locked == 0

    async def test_sequential_ids(self):
        venue = make()
        a = await venue.place_limit(order_request(cid="t-1"))
        b = await venue.place_limit(order_request(cid="t-2"))
        assert (a.order_id, b.order_id) == ("dry-nk-1", "dry-nk-2")

    async def test_no_simulated_balance_rejected(self):
        venue = make(btc="0.00001")
        with pytest.raises(VenueError, match="saldo insuficiente"):
            await venue.place_limit(order_request())
        assert await venue.open_orders() == []

    async def test_cancelling_releases_balance(self):
        venue = make()
        order = await venue.place_limit(order_request())
        await venue.cancel(order.order_id)
        assert await venue.open_orders() == []
        bal = await venue.balances()
        assert bal["BTC"] == Balance("BTC", D("0.005"), D("0"))

    async def test_cancel_missing_order(self):
        with pytest.raises(VenueError, match="dry-nk-9"):
            await make().cancel("dry-nk-9")

    async def test_cancel_every_order(self):
        venue = make()
        await venue.place_limit(order_request(cid="t-1"))
        await venue.place_limit(order_request("sell", cid="t-2"))
        await venue.cancel_all()
        assert await venue.open_orders() == []
        assert all(b.locked == 0 for b in (await venue.balances()).values())

    async def test_initial_balances_not_shared(self):
        initial = balances()
        venue = DryRunVenue(FakeFeed("nk"), initial, clock=lambda: 1)
        await venue.place_limit(order_request())
        assert initial["BTC"].locked == 0


@pytest.mark.spec("RF-13")
async def test_private_stream_reports_each_change():
    venue = make()
    seen = []

    async def read():
        async for ev in venue.private_stream():
            seen.append(ev)

    task = asyncio.create_task(read())
    await asyncio.sleep(0)
    order = await venue.place_limit(order_request())
    await venue.cancel(order.order_id)
    await asyncio.sleep(0.01)
    task.cancel()
    assert [o.status for o in seen] == ["open", "cancelled"]


@pytest.mark.spec("RF-13", "RF-09")
class TestSimulatedFill:
    """For tests only (T-18): the terminal's dry-run does not simulate fills (D-13)."""

    async def test_partial_and_complete_buy(self):
        venue = make()
        order = await venue.place_limit(order_request(qty="0.01", price="0.00730000"))
        partial = await venue.simulate_fill(order.order_id, D("0.004"))
        assert (partial.status, partial.filled) == ("partial", D("0.004"))
        bal = await venue.balances()
        assert bal["BTCB2"].available == D("0.504")
        full = await venue.simulate_fill(order.order_id, D("0.006"))
        assert (full.status, full.filled) == ("filled", D("0.01"))
        bal = await venue.balances()
        spent = D("0.01") * D("0.00730000") * D("1.002")
        assert bal["BTC"] == Balance("BTC", D("0.005") - spent, D("0"))
        assert bal["BTCB2"].available == D("0.51")
        assert await venue.open_orders() == []

    async def test_sell_collects_btc_minus_fee(self):
        venue = make()
        order = await venue.place_limit(order_request("sell", qty="0.2"))
        await venue.simulate_fill(order.order_id, D("0.2"))
        bal = await venue.balances()
        assert bal["BTCB2"] == Balance("BTCB2", D("0.3"), D("0"))
        assert bal["BTC"].available == D("0.005") + D("0.2") * D("0.00730000") * D("0.998")

    async def test_notifies_order_and_fill(self):
        venue = make()
        seen = []

        async def read():
            async for ev in venue.private_stream():
                seen.append(ev)

        task = asyncio.create_task(read())
        await asyncio.sleep(0)
        order = await venue.place_limit(order_request())
        await venue.simulate_fill(order.order_id, D("0.01"))
        await asyncio.sleep(0.01)
        task.cancel()
        kinds = [type(e).__name__ for e in seen]
        assert kinds == ["Order", "Order", "Fill"]
        fill = seen[2]
        assert (fill.order_id, fill.qty, fill.fee) == (
            "dry-nk-1",
            D("0.01"),
            D("0.01") * D("0.00730000") * D("0.002"),
        )

    async def test_cannot_fill_beyond_pending(self):
        venue = make()
        order = await venue.place_limit(order_request())
        with pytest.raises(VenueError, match="queda"):
            await venue.simulate_fill(order.order_id, D("0.02"))


class RealAccount(FakeFeed):
    """Venue with 'real' balances the test can change or make fail."""

    def __init__(self):
        super().__init__("nk")
        self.real = {
            "BTC": Balance("BTC", D("0.003"), D("0.001")),
            "BTCB2": Balance("BTCB2", D("0.2")),
        }
        self.fail = None

    async def balances(self):
        if self.fail:
            raise self.fail
        return dict(self.real)


@pytest.mark.spec("RF-13", "RF-10")
class TestRealBalances:
    """D-16: without fixed balances, dry-run starts from the account's real balances."""

    async def test_no_orders_real_balances(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        assert await venue.balances() == inner.real

    async def test_simulated_order_locks_over_real(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        await venue.place_limit(order_request(qty="0.01", price="0.00730000"))
        lock = D("0.01") * D("0.00730000") * D("1.002")
        bal = await venue.balances()
        assert bal["BTC"] == Balance("BTC", D("0.003") - lock, D("0.001") + lock)
        # the real ones are not touched
        assert inner.real["BTC"] == Balance("BTC", D("0.003"), D("0.001"))

    async def test_real_changes_are_seen(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        await venue.place_limit(order_request("sell", qty="0.1"))
        inner.real["BTCB2"] = Balance("BTCB2", D("0.7"))  # e.g. a deposit
        bal = await venue.balances()
        assert bal["BTCB2"] == Balance("BTCB2", D("0.6"), D("0.1"))

    async def test_real_balance_limits_simulated_orders(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        with pytest.raises(VenueError, match="saldo insuficiente"):
            await venue.place_limit(order_request("sell", qty="0.3"))

    async def test_cancelling_returns_to_the_real_balance(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        order = await venue.place_limit(order_request())
        await venue.cancel(order.order_id)
        assert await venue.balances() == inner.real

    async def test_read_failure_propagates(self):
        inner = RealAccount()
        inner.fail = VenueError("NonKYC: HTTP 429")
        venue = DryRunVenue(inner, clock=lambda: 1)
        with pytest.raises(VenueError, match="429"):
            await venue.balances()


@pytest.mark.spec("RNF-11")
class TestNoWaiting:
    """D-18: sending in dry-run must make no REST requests."""

    async def test_uses_hub_spec(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, spec=lambda: spec("nk"), clock=lambda: 1)
        await venue.place_limit(order_request())
        assert "market" not in inner.calls

    async def test_no_hub_spec_asks_for_it(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, spec=lambda: None, clock=lambda: 1)
        await venue.place_limit(order_request())
        assert inner.calls == ["market"]

    async def test_reuses_last_balance_read(self):
        now = {"t": 1_000}
        inner = RealAccount()
        reads = []
        real_balances = inner.balances

        async def counted():
            reads.append(now["t"])
            return await real_balances()

        inner.balances = counted
        venue = DryRunVenue(inner, spec=lambda: spec("nk"), clock=lambda: now["t"])
        await venue.balances()  # what AccountService does every ≤ 10 s
        now["t"] += 29_000
        await venue.place_limit(order_request())
        assert reads == [1_000]
        now["t"] += 2_000  # 31 s since the read: read again
        await venue.place_limit(order_request(cid="t-2"))
        assert reads == [1_000, 32_000]
