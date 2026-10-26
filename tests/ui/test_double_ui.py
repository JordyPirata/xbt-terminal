"""Orden doble desde la terminal (T-18): `dbl`, una confirmación con el spread,
panel de piernas y alerta de pierna colgada. Dos DryRunVenue y AccountService,
como en dry-run con keys de los dos venues."""

import asyncio
import json

import pytest

from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, BookSnapshot, ConnState, D, Level
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.account import AccountService
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import OrderService
from xbt_terminal.ui.app import ConfirmScreen
from xbt_terminal.ui.panels import (
    BalancesPanel,
    BookPanel,
    ChartPanel,
    CommandLine,
    LegsPanel,
)

from ..fakes import T0, FakeFeed
from ..services.test_orders import LIMITS
from .local import local_app

NOW = T0 + 60_000
DBL = "dbl 0.01 nk 0.00725000 nx 0.00726000"


class Rig:
    def __init__(self, tmp_path):
        self.now = NOW
        self.feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.hub = MarketHub(self.feeds, history=50, retry_s=0.01, clock=lambda: self.now)
        bal = {"BTC": Balance("BTC", D("0.005")), "BTCB2": Balance("BTCB2", D("0.5"))}
        self.venues = {
            vid: DryRunVenue(self.feeds[vid], bal, clock=lambda: self.now) for vid in ("nk", "nx")
        }
        self.logs = tmp_path / "logs"
        journal = Journal(self.logs, "dry-run", Redactor([]))
        self.orders = OrderService(self.hub, self.venues, journal, LIMITS, clock=lambda: self.now)
        self.account = AccountService(
            self.venues, journal=journal, retry_s=0.01, clock=lambda: self.now
        )
        self.app = local_app(
            self.hub,
            book_levels=15,
            timeframe="1m",
            mode="dry-run",
            can_trade={"nk": True, "nx": True},
            orders=self.orders,
            account=self.account,
            clock=lambda: self.now,
        )

    def feed_books(self):
        for vid, (bid, ask) in {
            "nk": ("0.00720000", "0.00730000"),
            "nx": ("0.00722000", "0.00732000"),
        }.items():
            snap = BookSnapshot(vid, (Level(D(bid), D("1")),), (Level(D(ask), D("1")),), self.now)
            self.feeds[vid].push(ConnState(vid, "market", "ws", "up", 100.0, self.now), snap)

    def events(self):
        files = sorted(self.logs.glob("journal-*.jsonl"))
        return [json.loads(ln)["event"] for f in files for ln in f.read_text().splitlines()]


async def until(pilot, predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await pilot.pause(0.01)


async def ready(rig, pilot):
    rig.feed_books()
    await until(
        pilot,
        lambda: all(
            rig.hub.state[v].ready and rig.hub.state[v].book.valid for v in "nk nx".split()
        ),
    )


async def send_double(rig, pilot):
    cmd = rig.app.query_one(CommandLine)
    cmd.focus()
    cmd.value = DBL
    await pilot.press("enter")
    await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))


@pytest.mark.spec("RF-07")
class TestOrdenDoble:
    async def test_una_confirmacion_con_las_dos_piernas_y_el_spread(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await send_double(rig, pilot)
            summary = rig.app.screen.summary
            assert "COMPRA 0.0100 XBT @ 0.00725000 en NonKYC" in summary
            assert "VENTA 0.01000000 XBT @ 0.00726000 en Neoxa" in summary
            assert "spread bruto +0.00000010 BTC" in summary
            await pilot.press("y")
            await until(pilot, lambda: "orden doble" in rig.app.last_message)
            assert "enviada" in rig.app.last_message
            assert [len(await v.open_orders()) for v in rig.venues.values()] == [1, 1]
            assert rig.events().count("order_ack") == 2

    async def test_cancelar_el_dialogo_no_envia_nada(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await send_double(rig, pilot)
            await pilot.press("escape")
            await until(pilot, lambda: "no se envió nada" in rig.app.last_message)
            assert rig.events() == []

    async def test_rechazo_sin_dialogo(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            cmd = rig.app.query_one(CommandLine)
            cmd.focus()
            cmd.value = "dbl 0.6 nk 0.00725000 nx 0.00726000"  # > 25 % del inventario
            await pilot.press("enter")
            await until(pilot, lambda: "no se envía ninguna pierna" in rig.app.last_message)
            assert not isinstance(rig.app.screen, ConfirmScreen)
            assert rig.events() == ["reject_local", "reject_local"]


@pytest.mark.spec("RF-07", "CA-03", "RNF-02")
class TestPanelDePiernas:
    async def test_oculto_hasta_la_primera_orden_doble(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            assert not rig.app.query_one(LegsPanel).display
            assert rig.app.query_one(BalancesPanel).region.width == 160  # todo el ancho

    async def test_muestra_cada_pierna_y_sigue_cabiendo(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await send_double(rig, pilot)
            await pilot.press("y")
            panel = rig.app.query_one(LegsPanel)
            await until(pilot, lambda: panel.display and "abierta" in panel.text)
            buy, sell, status = panel.text.splitlines()
            assert buy.split()[:2] == ["COMPRA", "NonKYC"] and buy.endswith("abierta 0.0000")
            assert sell.split()[:2] == ["VENTA", "Neoxa"] and sell.endswith("abierta 0.00000000")
            assert "sin cobertura automática" in status
            assert "dry-" not in panel.text  # el id de orden está en `o` (D-67)
            assert all(len(ln) <= 62 for ln in panel.text.splitlines())
            region = panel.region
            assert region.y + region.height <= 45 - 3  # encima de la línea de comandos
            # al mismo nivel que los balances, a su derecha (D-67)
            balances = rig.app.query_one(BalancesPanel).region
            assert (region.y, region.height) == (balances.y, balances.height)
            assert balances.right == region.x and region.right == 160 and region.width == 64
            assert all(c.region.height >= 8 for c in rig.app.query(ChartPanel))
            assert all(b.region.height == 19 for b in rig.app.query(BookPanel))

    async def test_un_fill_se_ve_en_la_pierna(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await send_double(rig, pilot)
            await pilot.press("y")
            panel = rig.app.query_one(LegsPanel)
            await until(pilot, lambda: "abierta" in panel.text)
            (state,) = rig.orders.doubles.values()
            await rig.venues["nk"].simulate_fill(state.buy.order_id, D("0.004"))
            await until(pilot, lambda: "parcial 0.0040" in panel.text)

    async def test_pierna_colgada_en_rojo_y_en_el_journal(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await send_double(rig, pilot)
            await pilot.press("y")
            panel = rig.app.query_one(LegsPanel)
            await until(pilot, lambda: "abierta" in panel.text)
            (state,) = rig.orders.doubles.values()
            await rig.venues["nk"].simulate_fill(state.buy.order_id, D("0.01"))
            await until(pilot, lambda: "llena 0.0100" in panel.text)
            rig.now += 31_000
            await until(pilot, lambda: "PIERNA COLGADA" in panel.text, timeout=3.0)
            assert "venta en Neoxa" in panel.text
            assert "pierna colgada" in rig.app.last_message
            assert rig.events().count("hanging_leg") == 1
            assert "red" in str(panel.rendered.spans[-1].style)
