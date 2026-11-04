"""Help inside the app (T-21, RF-15): `?` or `help`."""

import pytest

from xbt_terminal.ui.panels import CommandLine
from xbt_terminal.ui.screens.help import HelpScreen

from .test_double_ui import Rig, until


@pytest.mark.spec("RF-15")
class TestHelp:
    async def test_question_mark_opens_and_esc_closes(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.press("question_mark")
            await until(pilot, lambda: isinstance(rig.app.screen, HelpScreen))
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(rig.app.screen, HelpScreen))

    async def test_help_command_opens(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            cmd = rig.app.query_one(CommandLine)
            cmd.focus()
            cmd.value = "help"
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(rig.app.screen, HelpScreen))

    async def test_content(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.press("question_mark")
            await until(pilot, lambda: isinstance(rig.app.screen, HelpScreen))
            text = rig.app.screen.text
            assert "DRY-RUN" in text  # the current mode, explained
            for needle in (
                "buy nk 0.01 0.00725000",
                "dbl 0.01 nk 0.00725000 nx 0.00730000",
                "cancel all",
                "orders",
                "[y]",
                "clic",
                "privado",
                " A ",
            ):
                assert needle in text, needle
            for key in ("b ", "s ", "d ", "o ", "x ", "1 2 3", "q "):
                assert key in text, key

    async def test_visible_brackets(self, tmp_path):
        from textual.widgets import Static

        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.press("question_mark")
            await until(pilot, lambda: isinstance(rig.app.screen, HelpScreen))
            await pilot.pause()
            assert "[y]" in str(rig.app.screen.query_one(Static).render())

    async def test_inside_the_line_it_types(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            cmd = rig.app.query_one(CommandLine)
            cmd.focus()
            await pilot.press("question_mark")
            await pilot.pause()
            assert cmd.value == "?" and not isinstance(rig.app.screen, HelpScreen)

    async def test_placeholder_mentions_it(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            assert "? ayuda" in rig.app.query_one(CommandLine).placeholder
