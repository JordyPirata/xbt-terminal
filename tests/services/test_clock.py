"""Local clock and skew with the venue (T-29, RNF-16, D-30).

Book freshness uses the local arrival time, not the exchange's. In live, a venue
skewed more than `max_clock_skew_s`, or with no measured skew, gets no orders; in
dry-run it validates the same."""

from dataclasses import replace

import pytest

from xbt_terminal.services.orders import OrderService, Pending, Rejected

from .test_orders import LIMITS, NOW, Setup, buy, codes


def live(s, skew_ms, *, limits=LIMITS):
    """The OrderService of `s`, in live, with NonKYC's skew measured at `skew_ms`."""
    s.hub._venues["nk"].clock_skew_ms = skew_ms
    return OrderService(s.hub, {"nk": s.venue}, s.journal, limits, clock=lambda: NOW, live=True)


@pytest.mark.spec("RNF-16", "RF-10")
class TestFreshnessWithLocalClock:
    @pytest.mark.parametrize("server", [NOW - 60_000, NOW + 60_000])
    def test_skewed_exchange_time_does_not_count(self, tmp_path, server):
        s = Setup(tmp_path)
        s.book("nk", "0.00720000", "0.00730000", ts=server, received=NOW)
        assert s.hub.state["nk"].book.received_at == NOW

    @pytest.mark.parametrize("server", [NOW - 60_000, NOW + 60_000])
    async def test_freshly_received_book_counts(self, tmp_path, server):
        s = Setup(tmp_path)
        s.book("nk", "0.00720000", "0.00730000", ts=server, received=NOW - 1_000)
        assert isinstance(await s.orders.prepare(buy()), Pending)

    async def test_book_received_long_ago_invalid_even_if_exchange_says_now(self, tmp_path):
        s = Setup(tmp_path)
        s.book("nk", "0.00720000", "0.00730000", ts=NOW, received=NOW - 20_000)
        assert "stale_data" in codes(await s.orders.prepare(buy()))


@pytest.mark.spec("RNF-16")
class TestSkewInLive:
    async def test_within_cap_trades(self, tmp_path):
        s = Setup(tmp_path)
        assert isinstance(await live(s, 4_000).prepare(buy()), Pending)

    @pytest.mark.parametrize("skew", [6_000, -6_000])
    async def test_above_cap_rejected(self, tmp_path, skew):
        s = Setup(tmp_path)
        result = await live(s, skew).prepare(buy())
        assert isinstance(result, Rejected) and "clock_skew" in result.codes
        assert "6 s" in result.message and "NTP" in result.message

    async def test_unmeasured_is_rejected(self, tmp_path):
        """When in doubt, do not send: without a Date header it is unknown."""
        s = Setup(tmp_path)
        result = await live(s, None).prepare(buy())
        assert "clock_skew" in codes(result) and "sin medir" in result.message

    async def test_cap_comes_from_config(self, tmp_path):
        s = Setup(tmp_path)
        orders = live(s, 8_000, limits=replace(LIMITS, max_clock_skew_s=10))
        assert isinstance(await orders.prepare(buy()), Pending)

    async def test_dry_run_does_not_reject(self, tmp_path):
        s = Setup(tmp_path)
        s.hub._venues["nk"].clock_skew_ms = 60_000
        assert isinstance(await s.orders.prepare(buy()), Pending)

    def test_hub_gives_adapter_skew(self, tmp_path):
        s = Setup(tmp_path)
        assert s.hub.clock_skew_ms("nk") is None  # the fake venue does not measure it
        s.hub._venues["nk"].clock_skew_ms = 1_500
        assert s.hub.clock_skew_ms("nk") == 1_500
