"""Withdrawal fees service for the rebalance plan (T-45, RF-29, CA-14): one per venue
and asset, asked on each load, with the error isolated."""

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.core.models import D, WithdrawalFee
from xbt_terminal.services.rebalance import RebalanceService

FEES = {
    ("nk", "BTC"): "0.00005", ("nk", "BTCB2"): "0.0027",
    ("nx", "BTC"): "0.0001", ("nx", "BTCB2"): "0.0001",
}  # fmt: skip


class FakeFees:
    def __init__(self, venue, fail=None, fees=FEES):
        self.venue, self.fail, self.fees, self.calls = venue, fail or {}, fees, []

    async def withdrawal_fee(self, asset):
        self.calls.append(asset)
        if asset in self.fail:
            raise self.fail[asset]
        fee = D(self.fees[(self.venue, asset)])
        return WithdrawalFee(self.venue, asset, asset, fee, None, True)


@pytest.mark.spec("RF-29", "CA-14")
class TestLoad:
    async def test_one_per_venue_and_asset(self):
        got = await RebalanceService({"nk": FakeFees("nk"), "nx": FakeFees("nx")}).load()
        assert {k: fee_info.fee for k, fee_info in got.items()} == {
            k: D(fee_info) for k, fee_info in FEES.items()
        }

    async def test_error_stays_in_its_asset(self):
        nk = FakeFees("nk", fail={"BTC": VenueError("NonKYC: HTTP 500")})
        nx = FakeFees("nx", fail={"BTCB2": httpx.ConnectTimeout("")})
        got = await RebalanceService({"nk": nk, "nx": nx}).load()
        assert got[("nk", "BTC")] == "NonKYC: HTTP 500"
        assert got[("nx", "BTCB2")] == "ConnectTimeout"  # never empty (D-51)
        assert isinstance(got[("nk", "BTCB2")], WithdrawalFee)

    async def test_without_cache(self):
        nk = FakeFees("nk")
        svc = RebalanceService({"nk": nk})
        await svc.load()
        await svc.load()
        assert sorted(nk.calls) == ["BTC", "BTC", "BTCB2", "BTCB2"]
        assert svc.venues == ("nk",)
