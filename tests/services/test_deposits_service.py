"""Deposit addresses service (T-42, RF-28, CA-13): one row per venue and asset, asked on
each load, with a venue's error isolated."""

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.core.models import DepositAddress
from xbt_terminal.services.deposits import DepositRow, DepositService


class FakeDepositor:
    def __init__(self, venue, fail=None):
        self.venue, self.fail, self.calls = venue, fail or {}, []

    async def deposit_address(self, asset):
        self.calls.append(asset)
        if asset in self.fail:
            raise self.fail[asset]
        return DepositAddress(self.venue, asset, asset.lower(), "red", f"{self.venue}-{asset}")


@pytest.mark.spec("RF-28", "CA-13")
class TestLoad:
    async def test_one_row_per_venue_and_asset(self):
        svc = DepositService({"nk": FakeDepositor("nk"), "nx": FakeDepositor("nx")})
        rows = await svc.load()
        assert [(r.venue, r.asset, r.address.address) for r in rows] == [
            ("nk", "BTC", "nk-BTC"), ("nk", "BTCB2", "nk-BTCB2"),
            ("nx", "BTC", "nx-BTC"), ("nx", "BTCB2", "nx-BTCB2"),
        ]  # fmt: skip
        assert all(r.error == "" for r in rows)

    async def test_error_stays_in_its_row(self):
        nk = FakeDepositor("nk", fail={"BTC": VenueError("NonKYC: HTTP 500")})
        nx = FakeDepositor("nx", fail={"BTCB2": httpx.ConnectError("sin red")})
        rows = await DepositService({"nk": nk, "nx": nx}).load()
        assert rows[0] == DepositRow("nk", "BTC", None, "NonKYC: HTTP 500")
        assert rows[1].address is not None and rows[2].address is not None
        assert rows[3].address is None and "sin red" in rows[3].error

    async def test_without_cache(self):
        nk = FakeDepositor("nk")
        svc = DepositService({"nk": nk})
        await svc.load()
        await svc.load()
        assert nk.calls == ["BTC", "BTCB2", "BTC", "BTCB2"]

    async def test_only_given_venues(self):
        svc = DepositService({"nx": FakeDepositor("nx")})
        assert svc.venues == ("nx",)
        assert {r.venue for r in await svc.load()} == {"nx"}
