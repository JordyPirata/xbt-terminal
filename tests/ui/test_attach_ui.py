"""TUI conectada al daemon (T-53, T-63, RF-31, CA-15, CA-16, D-61, D-66): ve lo
del daemon, opera por el socket y salir no le cambia nada."""

import pytest

from xbt_terminal.core.bot import BotState
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.remote import RemoteEngine
from xbt_terminal.services.wire import ClientView
from xbt_terminal.ui.app import ClientServices, XbtApp
from xbt_terminal.ui.screens.confirm import ConfirmScreen
from xbt_terminal.ui.screens.orders import OrdersScreen
from xbt_terminal.ui.status import StatusBar

from ..services.test_remote import Daemon
from .test_orders_ui import NOW, type_command, until


class AttachRig:
    def __init__(self, tmp_path):
        self.daemon = Daemon(tmp_path)
        self.remote = RemoteEngine(self.daemon.path, has_bot=True, retry_s=0.02)
        self.hub = self.remote.market  # el mercado llega del daemon (D-66)
        services = ClientServices(
            orders=self.remote.orders,
            account=self.remote.account,
            bot=self.remote.bot,
            results=self.remote.results,
            deposits=self.remote.deposits,
            rebalance=self.remote.rebalance,
            remote=self.remote,
        )
        self.app = XbtApp(
            self.hub,
            view=ClientView(15, "1m", {}, None, None),
            mode="dry-run",
            engine=Engine(self.hub, mode="dry-run"),
            services=services,
            run_hub=False,
            clock=lambda: NOW,
        )

    async def __aenter__(self):
        await self.daemon.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.daemon.__aexit__(*exc)


def bar(app):
    return app.query_one(StatusBar).text


async def linked(rig, pilot):
    await until(pilot, lambda: rig.remote.snapshot is not None and "daemon" in bar(rig.app))


@pytest.mark.spec("RF-31", "CA-15")
class TestObservar:
    async def test_la_barra_dice_que_esta_conectada(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            assert "⇄ daemon" in bar(rig.app)

    async def test_si_el_daemon_se_va_lo_dice_y_vuelve(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await rig.daemon.server.aclose()
            await until(pilot, lambda: "sin daemon" in bar(rig.app))
            assert "conexión con el daemon perdida" in rig.app.last_message
            await rig.daemon.server.start()
            await until(pilot, lambda: "⇄ daemon" in bar(rig.app))

    async def test_los_avisos_del_daemon_salen_en_pantalla(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            rig.daemon.engine._say("⚠ pierna colgada en d-1: venta en Neoxa", error=True)
            await until(pilot, lambda: "pierna colgada" in rig.app.last_message)
            assert rig.app.last_error

    async def test_inciertas_del_daemon_en_la_barra(self, tmp_path):
        from ..services.test_uncertain import buy

        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            rig.daemon.s.lose_reply(create=False)
            await rig.daemon.s.send(buy())
            await until(pilot, lambda: "1 incierta" in bar(rig.app))


@pytest.mark.spec("RF-31", "RF-18", "CA-15")
class TestBot:
    async def test_bot_on_confirma_y_arma_el_del_daemon(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await type_command(pilot, rig.app, "bot on")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert rig.app.screen.summary.startswith("BOT DE ARBITRAJE")
            await pilot.press("y")
            await until(pilot, lambda: rig.daemon.bot.state.status == "armed")
            await until(pilot, lambda: "BOT armado" in bar(rig.app))

    async def test_ctrl_b_lo_para(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            rig.daemon.bot._set(BotState("armed"))
            await linked(rig, pilot)
            await until(pilot, lambda: rig.remote.bot.state.status == "armed")
            await pilot.press("ctrl+b")
            await until(pilot, lambda: rig.daemon.bot.state.status == "off")

    async def test_bot_status_con_el_texto_del_daemon(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await type_command(pilot, rig.app, "bot status")
            assert rig.app.last_message == "bot off"


@pytest.mark.spec("RF-31", "RF-06", "RF-08", "CA-16")
class TestOpera:
    async def test_una_orden_se_confirma_y_la_envia_el_daemon(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "BUY" in rig.app.screen.summary
            await pilot.press("y")
            await until(pilot, lambda: rig.app.last_message.startswith("enviada"))
            assert rig.daemon.s.events()[-2:] == ["order_sent", "order_ack"]

    async def test_la_rechaza_el_daemon(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 100 0.00725000")
            await until(pilot, lambda: rig.app.last_error)
            assert "rechazada" in rig.app.last_message
            assert not isinstance(rig.app.screen, ConfirmScreen)

    async def test_cancelar_todo(self, tmp_path):
        from ..services.test_uncertain import buy

        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await rig.daemon.s.send(buy())
            await linked(rig, pilot)
            await pilot.press("x")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "cancelado" in rig.app.last_message)
            assert await rig.daemon.s.venue.open_orders() == []

    async def test_la_tecla_o_abre_las_ordenes(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await pilot.press("o")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))

    async def test_sin_daemon_lo_dice(self, tmp_path):
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await rig.daemon.server.aclose()
            await until(pilot, lambda: "sin daemon" in bar(rig.app))
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: "sin conexión con el daemon" in rig.app.last_message)

    async def test_depositos_del_daemon(self, tmp_path):
        """El Rig del daemon no tiene keys para depósitos: lo dice, sin abrir la ventana."""
        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await pilot.press("w")
            assert rig.app.last_error and "depósitos" in rig.app.last_message

    async def test_resultados_del_daemon(self, tmp_path):
        from xbt_terminal.ui.screens.results import ResultsScreen

        async with AttachRig(tmp_path) as rig, rig.app.run_test(size=(160, 45)) as pilot:
            await linked(rig, pilot)
            await pilot.press("r")
            await until(pilot, lambda: isinstance(rig.app.screen, ResultsScreen))
            await until(pilot, lambda: "resultados" in rig.app.last_message)  # sin servicio


@pytest.mark.spec("RF-31", "CA-15")
class TestSalir:
    async def test_salir_no_toca_el_daemon(self, tmp_path):
        async with AttachRig(tmp_path) as rig:
            rig.daemon.bot._set(BotState("armed"))
            async with rig.app.run_test(size=(160, 45)) as pilot:
                await linked(rig, pilot)
                await pilot.press("q")
                await until(pilot, lambda: not rig.app.is_running)
            assert rig.daemon.bot.state.status == "armed"
            assert rig.daemon.s.events() == []
