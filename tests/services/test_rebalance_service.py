"""Servicio de comisiones de retiro para el plan de reequilibrio (T-45, RF-29,
CA-14): una por venue y activo, pedidas en cada carga y con el error aislado."""

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
class TestCarga:
    async def test_una_por_venue_y_activo(self):
        got = await RebalanceService({"nk": FakeFees("nk"), "nx": FakeFees("nx")}).load()
        assert {k: v.fee for k, v in got.items()} == {k: D(v) for k, v in FEES.items()}

    async def test_el_error_queda_en_su_activo(self):
        nk = FakeFees("nk", fail={"BTC": VenueError("NonKYC: HTTP 500")})
        nx = FakeFees("nx", fail={"BTCB2": httpx.ConnectTimeout("")})
        got = await RebalanceService({"nk": nk, "nx": nx}).load()
        assert got[("nk", "BTC")] == "NonKYC: HTTP 500"
        assert got[("nx", "BTCB2")] == "ConnectTimeout"  # nunca vacío (D-51)
        assert isinstance(got[("nk", "BTCB2")], WithdrawalFee)

    async def test_sin_cache(self):
        nk = FakeFees("nk")
        svc = RebalanceService({"nk": nk})
        await svc.load()
        await svc.load()
        assert sorted(nk.calls) == ["BTC", "BTC", "BTCB2", "BTCB2"]
        assert svc.venues == ("nk",)
