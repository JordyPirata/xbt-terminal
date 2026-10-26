"""Servicio de direcciones de depósito (T-42, RF-28, CA-13): una fila por venue y
activo, pedidas en cada carga y con el error de un venue aislado."""

import httpx
import pytest

from btcb2_terminal.adapters.base import VenueError
from btcb2_terminal.core.models import DepositAddress
from btcb2_terminal.services.deposits import DepositRow, DepositService


class FakeDepositor:
    def __init__(self, venue, fail=None):
        self.venue, self.fail, self.calls = venue, fail or {}, []

    async def deposit_address(self, asset):
        self.calls.append(asset)
        if asset in self.fail:
            raise self.fail[asset]
        return DepositAddress(self.venue, asset, asset.lower(), "red", f"{self.venue}-{asset}")


@pytest.mark.spec("RF-28", "CA-13")
class TestCarga:
    async def test_una_fila_por_venue_y_activo(self):
        svc = DepositService({"nk": FakeDepositor("nk"), "nx": FakeDepositor("nx")})
        rows = await svc.load()
        assert [(r.venue, r.asset, r.address.address) for r in rows] == [
            ("nk", "BTC", "nk-BTC"), ("nk", "BTCB2", "nk-BTCB2"),
            ("nx", "BTC", "nx-BTC"), ("nx", "BTCB2", "nx-BTCB2"),
        ]  # fmt: skip
        assert all(r.error == "" for r in rows)

    async def test_el_error_queda_en_su_fila(self):
        nk = FakeDepositor("nk", fail={"BTC": VenueError("NonKYC: HTTP 500")})
        nx = FakeDepositor("nx", fail={"BTCB2": httpx.ConnectError("sin red")})
        rows = await DepositService({"nk": nk, "nx": nx}).load()
        assert rows[0] == DepositRow("nk", "BTC", None, "NonKYC: HTTP 500")
        assert rows[1].address is not None and rows[2].address is not None
        assert rows[3].address is None and "sin red" in rows[3].error

    async def test_sin_cache(self):
        nk = FakeDepositor("nk")
        svc = DepositService({"nk": nk})
        await svc.load()
        await svc.load()
        assert nk.calls == ["BTC", "BTCB2", "BTC", "BTCB2"]

    async def test_solo_los_venues_dados(self):
        svc = DepositService({"nx": FakeDepositor("nx")})
        assert svc.venues == ("nx",)
        assert {r.venue for r in await svc.load()} == {"nx"}
