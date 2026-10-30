"""Órdenes desde la terminal en dry-run (T-16): comando, diálogo con nocional,
confirmar o cancelar, y mensajes."""

import asyncio
import json

import pytest

from xbt_terminal.adapters.base import UncertainError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, BookSnapshot, ConnState, D, Level
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import OrderService
from xbt_terminal.ui.panels import CommandLine
from xbt_terminal.ui.screens.confirm import ConfirmScreen
from xbt_terminal.ui.status import StatusBar

from ..fakes import T0, FakeFeed
from ..services.test_orders import LIMITS
from .local import local_app

NOW = T0 + 60_000


class Rig:
    def __init__(self, tmp_path, mode="dry-run", **app_opts):
        self.feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.hub = MarketHub(self.feeds, history=50, retry_s=0.01, clock=lambda: NOW)
        balances = {"BTC": Balance("BTC", D("0.005")), "BTCB2": Balance("BTCB2", D("0.5"))}
        self.venue = DryRunVenue(self.feeds["nk"], balances, clock=lambda: NOW)
        self.logs = tmp_path / "logs"
        orders = None
        if mode == "dry-run":
            journal = Journal(self.logs, mode, Redactor([]))
            orders = OrderService(self.hub, {"nk": self.venue}, journal, LIMITS, clock=lambda: NOW)
        self.app = local_app(
            self.hub,
            book_levels=15,
            timeframe="1m",
            mode=mode,
            can_trade={"nk": mode == "dry-run", "nx": False},
            orders=orders,
            clock=lambda: NOW,
            **app_opts,
        )

    def feed_book(self):
        snap = BookSnapshot(
            "nk", (Level(D("0.00720000"), D("1")),), (Level(D("0.00730000"), D("1")),), NOW
        )
        self.feeds["nk"].push(ConnState("nk", "market", "ws", "up", 100.0, NOW), snap)

    def events(self):
        files = sorted(self.logs.glob("journal-*.jsonl"))
        return [json.loads(ln)["event"] for f in files for ln in f.read_text().splitlines()]


async def until(pilot, predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await pilot.pause(0.01)


async def ready(rig, pilot):
    rig.feed_book()
    await until(pilot, lambda: rig.hub.state["nk"].ready and rig.hub.state["nk"].book.valid)


async def type_command(pilot, app, text):
    cmd = app.query_one(CommandLine)
    cmd.focus()
    cmd.value = text
    await pilot.press("enter")


@pytest.mark.spec("RF-06", "RF-13")
class TestOrdenConConfirmacion:
    async def test_dialogo_con_nocional_y_confirmar(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            dialog = rig.app.screen
            assert "BUY 0.0100 XBT @ 0.00725000" in dialog.summary
            assert "0.00007250 BTC" in dialog.summary and "DRY-RUN" in dialog.text
            await pilot.press("y")
            await until(pilot, lambda: rig.app.last_message.startswith("enviada"))
            assert "dry-nk-1" in rig.app.last_message
            assert [o.order_id for o in await rig.venue.open_orders()] == ["dry-nk-1"]
            assert rig.events() == ["order_sent", "order_ack"]

    @pytest.mark.spec("RF-17")
    async def test_cantidad_porcentual(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 25% 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "BUY 0.1720 XBT" in rig.app.screen.summary
            assert "25 % de 0.00500000 BTC en NonKYC" in rig.app.screen.text

    @pytest.mark.parametrize("key", ["n", "escape", "enter"])
    async def test_por_defecto_no_se_envia(self, tmp_path, key):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press(key)
            await until(pilot, lambda: not isinstance(rig.app.screen, ConfirmScreen))
            assert rig.app.last_message == "orden cancelada, no se envió nada"
            assert await rig.venue.open_orders() == [] and rig.events() == []

    async def test_rechazo_local_sin_dialogo(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.3 0.00725000")
            await until(pilot, lambda: "rechazada" in rig.app.last_message)
            assert not isinstance(rig.app.screen, ConfirmScreen)
            assert "25 % del inventario total" in rig.app.last_message
            assert rig.events() == ["reject_local"]

    async def test_comando_mal_escrito(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy zz 1 1")
            await until(pilot, lambda: "zz" in rig.app.last_message)
            assert rig.events() == []

    async def test_el_campo_se_vacia_tras_enviar(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.3 0.00725000")
            await until(pilot, lambda: rig.app.last_message != "")
            assert rig.app.query_one(CommandLine).value == ""


@pytest.mark.spec("RF-06", "RF-03")
class TestOtrosComandos:
    async def test_tf_cambia_el_timeframe(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await type_command(pilot, rig.app, "tf 1h")
            await until(pilot, lambda: rig.app.timeframe == "1h")

    @pytest.mark.parametrize("key,prefix", [("b", "buy "), ("s", "sell ")])
    async def test_atajos_b_y_s(self, tmp_path, key, prefix):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.press(key)
            cmd = rig.app.query_one(CommandLine)
            await until(pilot, lambda: cmd.has_focus and cmd.value == prefix)

    async def test_teclear_tras_el_atajo_no_borra_el_prefijo(self, tmp_path):
        """select_on_focus de Textual sustituía "buy " por la primera tecla (D-18)."""
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.press("b")
            await pilot.pause()
            await pilot.press("n", "k")
            await until(pilot, lambda: rig.app.query_one(CommandLine).value == "buy nk")

    async def test_clic_en_la_linea_con_texto_y_teclear_no_lo_borra(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            cmd = rig.app.query_one(CommandLine)
            cmd.value = "buy nk 0.01"
            await pilot.click("#cmd")
            await pilot.pause()
            await pilot.press("end", "space", "1")
            await until(pilot, lambda: cmd.value == "buy nk 0.01 1")

    async def test_escape_suelta_el_campo_y_vuelven_los_atajos(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.press("b")
            await pilot.press("escape")
            await pilot.press("3")
            await until(pilot, lambda: rig.app.timeframe == "1h")


@pytest.mark.spec("RF-13")
class TestSoloLectura:
    async def test_sin_keys_no_hay_comandos_de_orden(self, tmp_path):
        rig = Rig(tmp_path, mode="read-only")
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            cmd = rig.app.query_one(CommandLine)
            assert cmd.disabled and "solo lectura" in cmd.placeholder
            await pilot.press("b")
            await pilot.pause()
            assert not cmd.has_focus and cmd.value == ""


@pytest.mark.spec("RF-03")
async def test_al_arrancar_los_atajos_funcionan_en_dry_run(tmp_path):
    """La línea de comandos no debe quedarse el foco al arrancar."""
    rig = Rig(tmp_path)
    async with rig.app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await until(pilot, lambda: rig.app.timeframe == "5m")
        assert rig.app.query_one(CommandLine).value == ""


@pytest.mark.spec("RNF-11")
async def test_el_dialogo_no_espera_a_la_red(tmp_path):
    """D-18: con la cuenta al día, de Enter al diálogo sin peticiones lentas."""
    import time

    from xbt_terminal.services.account import AccountService

    rig = Rig(tmp_path)
    account = AccountService({"nk": rig.venue}, retry_s=0.01, clock=lambda: NOW)
    rig.app.account = rig.app.engine.account = account  # el motor la corre (D-61)
    rig.app.orders._account = account
    async with rig.app.run_test(size=(160, 45)) as pilot:
        await ready(rig, pilot)
        await until(pilot, lambda: account.state["nk"].balances is not None)

        async def slow():
            await asyncio.sleep(1.0)
            return {}

        rig.venue.balances = slow  # si se consultara, tardaría 1 s
        rig.venue.open_orders = slow
        start = time.perf_counter()
        await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
        await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen), timeout=0.9)
        assert time.perf_counter() - start < 0.3


def lose_reply(venue, *, create=True):
    """El siguiente envío pierde la respuesta (RNF-12); con `create`, la orden existe."""
    real = venue.place_limit

    async def lost(req):
        venue.place_limit = real
        if create:
            await real(req)
        raise UncertainError("NonKYC: sin respuesta (ReadTimeout)")

    venue.place_limit = lost


@pytest.mark.spec("RNF-12")
class TestEnvioIncierto:
    async def test_aviso_en_rojo_y_en_la_barra_mientras_siga_incierta(self, tmp_path):
        rig = Rig(tmp_path, reconcile_every_s=60)
        lose_reply(rig.venue, create=False)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "INCIERTA" in rig.app.last_message)
            assert rig.app.last_error and "ReadTimeout" in rig.app.last_message
            await until(pilot, lambda: "1 incierta" in rig.app.query_one(StatusBar).text)
            assert rig.events() == ["order_sent", "order_unknown"]

    async def test_al_reconciliarla_se_avisa_y_sale_de_la_barra(self, tmp_path):
        rig = Rig(tmp_path, reconcile_every_s=0.05)
        lose_reply(rig.venue)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "dry-nk-1" in rig.app.last_message)
            assert "existe" in rig.app.last_message and not rig.app.last_error
            await until(pilot, lambda: "incierta" not in rig.app.query_one(StatusBar).text)
            assert rig.events() == ["order_sent", "order_unknown", "order_ack"]
