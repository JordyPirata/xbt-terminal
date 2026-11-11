"""The bot on screen (T-37, RF-18, RF-21, RF-23): command, confirmation on arming, stop
key, bar and notices. Real services (BotRig), app without launching its own."""

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
class TestArm:
    async def test_confirm_arms(self, tmp_path):
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
    async def test_does_not_arm_by_default(self, tmp_path, key):
        async with BotRig(tmp_path) as rig:
            app = app_for(rig)
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                await pilot.press(key)
                await until(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
                assert rig.bot.state.status == "off" and "no se armó" in app.last_message

    async def test_live_without_enabled_no_dialog(self, tmp_path):
        async with BotRig(tmp_path, live=True, enabled=False) as rig:
            app = app_for(rig, mode="live")
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: "enabled" in app.last_message)
                assert app.last_error and not isinstance(app.screen, ConfirmScreen)
                assert rig.bot.state.status == "off"

    async def test_live_warns_of_real_orders(self, tmp_path):
        async with BotRig(tmp_path, live=True) as rig:
            app = app_for(rig, mode="live")
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
                assert "LIVE · ARMAR EL BOT: ÓRDENES REALES AUTOMÁTICAS" in app.screen.text

    async def test_without_bot_in_config(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            app = app_for(rig)
            app.bot = None
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot on")
                await until(pilot, lambda: "[bot]" in app.last_message)
                assert app.last_error


@pytest.mark.spec("RF-18", "RF-23")
class TestStopping:
    async def test_p_key(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            rig.bot.arm()
            app = app_for(rig)
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.press("p")
                await until(pilot, lambda: rig.bot.state.status == "off")
                assert "bot parado" in app.last_message
                await until(pilot, lambda: "BOT" not in bar(app))

    async def test_off_command(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            rig.bot.arm()
            app = app_for(rig)
            async with app.run_test(size=(160, 45)) as pilot:
                await type_command(pilot, app, "bot off")
                await until(pilot, lambda: rig.bot.state.status == "off")


@pytest.mark.spec("RF-21", "RF-23")
async def test_stop_by_rejected_leg_shows_red(tmp_path):
    async def refuse(order_request):
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
async def test_status_says_state_and_why_not_firing(tmp_path):
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
async def test_help_explains_the_bot(tmp_path):
    async with BotRig(tmp_path) as rig:
        app = app_for(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("question_mark")
            await until(pilot, lambda: isinstance(app.screen, HelpScreen))
            text = app.screen.text
            assert "bot on" in text and "bot status" in text and "PARADO" in text
            assert "ctrl+b" in text


@pytest.mark.spec("RF-18")
async def test_stop_right_after_arming_from_the_line(tmp_path):
    """After `bot on` + y, focus returns to the command line, where `p` would be typed as
    text: ctrl+b stops the bot even with the line focused."""
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
