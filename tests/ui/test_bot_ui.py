"""El bot en la pantalla (T-37, RF-18, RF-21, RF-23): comando, confirmación al
armar, tecla de parada, barra y avisos. Servicios de verdad (BotRig), app sin
lanzar los suyos."""

import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.ui.panels import CommandLine
from xbt_terminal.ui.screens.confirm import ConfirmScreen
from xbt_terminal.ui.screens.help import HelpScreen
from xbt_terminal.ui.status import StatusBar

from ..services.test_bot_service import BotRig
from .local import local_app
from .test_orders_ui import type_command, until


def app_for(rig, mode="dry-run"):
    return local_app(
        rig.hub,
        book_levels=15,
        timeframe="1m",
        mode=mode,
        can_trade={"nk": True, "nx": True},
        orders=rig.orders,
        account=rig.account,
        bot=rig.bot,
        run_hub=False,
        clock=rig.clock,
    )


def bar(app):
    return app.query_one(StatusBar).text


@pytest.mark.spec("RF-18")
class TestArmar:
    async def test_confirmar_arma(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            app = app_for(rig)
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                text = app.screen.text
                assert "ARMAR EL BOT" in text and "DRY-RUN" in text
                assert "neta mínima 0.5 %" in text and "slippage máx. 0.2 %" in text
                assert "10 XBT por disparo" in text and "1 BTC de compra al día" in text
                await pilot.press("y")
                await until(pilot, lambda: rig.bot.state.status == "armed")
                await until(pilot, lambda: "BOT armado" in bar(app))
                assert "bot armado" in app.last_message

    @pytest.mark.parametrize("key", ["n", "escape", "enter"])
    async def test_por_defecto_no_se_arma(self, tmp_path, key):
        async with BotRig(tmp_path) as rig:
            app = app_for(rig)
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                await pilot.press(key)
                await until(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
                assert rig.bot.state.status == "off" and "no se armó" in app.last_message

    async def test_live_sin_enabled_ni_dialogo(self, tmp_path):
        async with BotRig(tmp_path, live=True, enabled=False) as rig:
            app = app_for(rig, mode="live")
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: "enabled" in app.last_message)
                assert app.last_error and not isinstance(app.screen, ConfirmScreen)
                assert rig.bot.state.status == "off"

    async def test_live_avisa_de_ordenes_reales(self, tmp_path):
        async with BotRig(tmp_path, live=True) as rig:
            app = app_for(rig, mode="live")
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                assert "LIVE · ARMAR EL BOT: ÓRDENES REALES AUTOMÁTICAS" in app.screen.text

    async def test_sin_bot_en_la_config(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            app = app_for(rig)
            app.bot = None
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: "[bot]" in app.last_message)
                assert app.last_error


@pytest.mark.spec("RF-18", "RF-23")
class TestParar:
    async def test_tecla_p(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            rig.bot.arm()
            app = app_for(rig)
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.press("p")
                await until(pilot, lambda: rig.bot.state.status == "off")
                assert "bot parado" in app.last_message
                await until(pilot, lambda: "BOT" not in bar(app))

    async def test_comando_off(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            rig.bot.arm()
            app = app_for(rig)
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot off")
                await until(pilot, lambda: rig.bot.state.status == "off")


@pytest.mark.spec("RF-21", "RF-23")
async def test_parada_por_pierna_rechazada_se_ve_en_rojo(tmp_path):
    async def refuse(req):
        raise VenueError("Neoxa: [400] rechazada")

    async with BotRig(tmp_path) as rig:
        rig.venues["nx"].place_limit = refuse
        app = app_for(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            rig.bot.arm()
            rig.books()
            await until(pilot, lambda: rig.bot.state.status == "halted")
            await until(pilot, lambda: "BOT ✖ PARADO" in bar(app))
            await until(pilot, lambda: "bot PARADO" in app.last_message)
            assert app.last_error and "rechazada" in app.last_message


@pytest.mark.spec("RF-23")
async def test_status_dice_estado_y_por_que_no_dispara(tmp_path):
    async with BotRig(tmp_path) as rig:
        app = app_for(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            rig.bot.arm()
            rig.books(nx=("0.00700500", "0.00730000"))
            await until(pilot, lambda: rig.bot.last_skip is not None)
            await type_command(pilot, app, "bot status")
            await until(pilot, lambda: app.last_message.startswith("bot armado"))
            assert "below_threshold" in app.last_message and "disparos hoy 0" in app.last_message


@pytest.mark.spec("RF-15", "RF-18")
async def test_la_ayuda_explica_el_bot(tmp_path):
    async with BotRig(tmp_path) as rig:
        app = app_for(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("question_mark")
            await until(pilot, lambda: isinstance(app.screen, HelpScreen))
            text = app.screen.text
            assert "bot on" in text and "bot status" in text and "PARADO" in text
            assert "ctrl+b" in text


@pytest.mark.spec("RF-18")
async def test_parar_justo_despues_de_armar_desde_la_linea(tmp_path):
    """Tras `bot on` + y, el foco vuelve a la línea de comandos, donde la `p` se
    escribiría como texto: ctrl+b para el bot aunque la línea tenga el foco."""
    async with BotRig(tmp_path) as rig:
        app = app_for(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            await type_command(pilot, app, "bot on")
            await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: rig.bot.state.status == "armed")
            assert app.query_one(CommandLine).has_focus
            await pilot.press("ctrl+b")
            await until(pilot, lambda: rig.bot.state.status == "off")
            assert app.query_one(CommandLine).value == ""
