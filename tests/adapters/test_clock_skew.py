"""Clock skew with each venue (T-29, RNF-16, D-30): measured with each REST response's
`Date` header, against the request's local time."""

import httpx
import pytest

from xbt_terminal.adapters.streaming import skew_ms

from .test_supervisor import FakeVenue

DATE = "Thu, 22 Oct 2026 02:07:27 GMT"
DATE_MS = 1_792_634_847_000  # that second in epoch ms


@pytest.mark.spec("RNF-16")
class TestSkew:
    def test_clock_on_time(self):
        """`Date` truncates to the second: the middle of the second is taken."""
        assert skew_ms(DATE, DATE_MS + 400, DATE_MS + 600) == 0

    @pytest.mark.parametrize("off", [60_000, -60_000])
    def test_skewed_local_clock(self, off):
        local = DATE_MS + 500 - off
        assert skew_ms(DATE, local - 100, local + 100) == off

    @pytest.mark.parametrize("bad", [None, "", "ayer", "Thu, 99 Oct 2026 02:07:27 GMT"])
    def test_unreadable_header(self, bad):
        assert skew_ms(bad, DATE_MS, DATE_MS) is None


@pytest.mark.spec("RNF-16")
class TestInAdapter:
    async def test_each_rest_response_updates_it(self):
        now = [DATE_MS + 500 - 30_000]
        venue = FakeVenue(clock=lambda: now[0])
        assert venue.clock_skew_ms is None

        async def reply():
            return httpx.Response(200, headers={"date": DATE})

        await venue.timed(reply())
        assert venue.clock_skew_ms == 30_000

    async def test_non_response_leaves_it_alone(self):
        venue = FakeVenue()

        async def other():
            return {"ok": True}

        await venue.timed(other())
        assert venue.clock_skew_ms is None
