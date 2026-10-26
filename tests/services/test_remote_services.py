"""Resultados, depósitos y comisiones de retiro por el socket del daemon (T-64,
RF-25, RF-28, RF-29, RF-31, D-66): el cliente no lee el journal ni las keys."""

import asyncio

import pytest

from btcb2_terminal.adapters.base import VenueError
from btcb2_terminal.core.models import D
from btcb2_terminal.services.control import ControlServer
from btcb2_terminal.services.deposits import DepositRow, DepositService
from btcb2_terminal.services.engine import Engine
from btcb2_terminal.services.rebalance import RebalanceService
from btcb2_terminal.services.remote import RemoteEngine
from btcb2_terminal.services.results import ResultsService

from ..ui.test_results_ui import write
from ..unit.test_results import T0, fill, plain_double
from ..unit.test_valuation import value_row
from .test_deposits_service import FakeDepositor
from .test_rebalance_service import FakeFees
from .test_uncertain import Rig


async def until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class Daemon:
    def __init__(self, tmp_path, *, deposits=None, rebalance=None, results=None):
        self.s = Rig(tmp_path)
        self.journal_dir = tmp_path / "j"
        self.engine = Engine(
            self.s.hub, mode="dry-run", orders=self.s.orders, journal=self.s.journal,
            results=results, deposits=deposits, rebalance=rebalance,
        )  # fmt: skip
        self.path = tmp_path / "s.sock"
        self.server = ControlServer(self.engine, self.path, every_s=0.05)

    async def __aenter__(self):
        await self.server.start()
        self.remote = RemoteEngine(self.path, has_bot=False, retry_s=0.02)
        self.task = asyncio.create_task(self.remote.run())
        await until(lambda: self.remote.connected and self.remote.snapshot is not None)
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        await self.remote.aclose()
        await self.server.aclose()


@pytest.mark.spec("RF-31", "RF-25", "CA-11")
class TestResultados:
    async def test_el_informe_lo_calcula_el_daemon(self, tmp_path):
        jdir = tmp_path / "j"
        write(jdir, "20261022", plain_double("d-1", T0))
        results = ResultsService(jdir, clock=lambda: T0)
        async with Daemon(tmp_path, results=results) as d:
            rr = d.remote.results
            assert rr.load("dia").summary.closed == 0  # aún sin pedir
            await rr.refresh("dia")
            report = rr.load("dia")
            assert [x.id for x in report.doubles] == ["d-1"]
            assert report == results.load("dia")

    async def test_con_el_precio_del_token(self, tmp_path):
        jdir = tmp_path / "j"
        rows = plain_double("d-1", T0)
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.0025", asset="NKYC")
        write(jdir, "20261022", rows)

        async def price():
            return D("0.0001")

        results = ResultsService(jdir, clock=lambda: T0, pricers={"NKYC": price})
        async with Daemon(tmp_path, results=results) as d:
            await d.remote.results.refresh("dia")
            summary = d.remote.results.load("dia").summary
            assert summary.prices == (("NKYC", D("0.0001")),)

    @pytest.mark.spec("RF-32", "CA-17")
    async def test_la_serie_del_valor_viaja_en_el_informe(self, tmp_path):
        jdir = tmp_path / "j"
        write(jdir, "20261022", [*plain_double("d-1", T0), value_row(T0, "1.5", nk="1", nx="0.5")])
        results = ResultsService(jdir, clock=lambda: T0)
        async with Daemon(tmp_path, results=results) as d:
            await d.remote.results.refresh("dia")
            values = d.remote.results.load("dia").values
            assert values == results.load("dia").values
            assert [v.value for v in values] == [D("1.5")]

    async def test_sin_resultados_en_el_daemon(self, tmp_path):
        async with Daemon(tmp_path) as d:
            await d.remote.results.refresh("dia")
            assert "resultados" in d.remote.results.error
            assert d.remote.results.load("dia").doubles == ()

    async def test_un_fill_llega_al_cliente(self, tmp_path):
        async with Daemon(tmp_path) as d:
            seen = []
            d.remote.account.subscribe(lambda vid, what: seen.append((vid, what)))
            d.engine.account = None  # el Rig no tiene cuenta: se avisa a mano
            d.server._on_account("nk", "fill")
            await until(lambda: ("nk", "fill") in seen)


@pytest.mark.spec("RF-31", "RF-28", "CA-13")
class TestDepositos:
    async def test_las_filas_del_daemon(self, tmp_path):
        deposits = DepositService({"nk": FakeDepositor("nk"), "nx": FakeDepositor("nx")})
        async with Daemon(tmp_path, deposits=deposits) as d:
            assert d.remote.deposits.venues == ("nk", "nx")
            rows = await d.remote.deposits.load()
            assert [(r.venue, r.asset, r.address.address) for r in rows] == [
                ("nk", "BTC", "nk-BTC"), ("nk", "BTCB2", "nk-BTCB2"),
                ("nx", "BTC", "nx-BTC"), ("nx", "BTCB2", "nx-BTCB2"),
            ]  # fmt: skip

    async def test_el_error_de_un_venue_viaja_en_su_fila(self, tmp_path):
        nk = FakeDepositor("nk", fail={"BTC": VenueError("NonKYC: HTTP 500")})
        async with Daemon(tmp_path, deposits=DepositService({"nk": nk})) as d:
            rows = await d.remote.deposits.load()
            assert rows[0] == DepositRow("nk", "BTC", None, "NonKYC: HTTP 500")

    async def test_sin_keys_no_hay_venues(self, tmp_path):
        async with Daemon(tmp_path) as d:
            assert d.remote.deposits.venues == ()

    async def test_sin_daemon_cada_fila_lo_dice(self, tmp_path):
        deposits = DepositService({"nk": FakeDepositor("nk")})
        async with Daemon(tmp_path, deposits=deposits) as d:
            await d.server.aclose()
            await until(lambda: not d.remote.connected)
            rows = await d.remote.deposits.load()
            assert rows and all(r.address is None and "daemon" in r.error for r in rows)


@pytest.mark.spec("RF-31", "RF-29", "CA-14")
class TestComisiones:
    async def test_las_del_daemon(self, tmp_path):
        nk = FakeFees("nk", fail={"BTCB2": VenueError("NonKYC: HTTP 500")})
        rebalance = RebalanceService({"nk": nk, "nx": FakeFees("nx")})
        async with Daemon(tmp_path, rebalance=rebalance) as d:
            assert d.remote.rebalance.venues == ("nk", "nx")
            fees = await d.remote.rebalance.load()
            assert fees[("nk", "BTC")].fee == D("0.00005")
            assert fees[("nk", "BTCB2")] == "NonKYC: HTTP 500"
            assert fees == await rebalance.load()

    async def test_sin_daemon(self, tmp_path):
        rebalance = RebalanceService({"nk": FakeFees("nk")})
        async with Daemon(tmp_path, rebalance=rebalance) as d:
            await d.server.aclose()
            await until(lambda: not d.remote.connected)
            fees = await d.remote.rebalance.load()
            assert all(isinstance(v, str) and "daemon" in v for v in fees.values())
