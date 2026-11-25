"""`watch` in the terminal (T-109, RF-37, RNF-25): the daemon keeps the list; the screen
shows it only when asked and says the refusals."""

import pytest

from xbt_terminal.services.remote import DaemonRefusedError
from xbt_terminal.services.wire import Watched
from xbt_terminal.ui.panels import CommandLine
from xbt_terminal.ui.screens.watch import WatchScreen

from .test_double_ui import Rig, until

ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


class FakeWatch:
    def __init__(self):
        self.sent = []
        self.refuse = None

    async def send(self, action, asset=None, address=None):
        self.sent.append((action, asset, address))
        if self.refuse:
            raise DaemonRefusedError(self.refuse)
        return Watched((ADDRESS,), ())


async def run(rig, pilot, text):
    command = rig.app.query_one(CommandLine)
    command.focus()
    command.value = text
    await pilot.press("enter")


@pytest.mark.spec("RF-37", "RNF-25")
class TestWatchCommand:
    async def test_list_opens_the_screen_with_the_addresses(self, tmp_path):
        rig = Rig(tmp_path)
        rig.app.watching = FakeWatch()
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await run(rig, pilot, "watch")
            await until(pilot, lambda: isinstance(rig.app.screen, WatchScreen))
            assert ADDRESS in rig.app.screen.text and "BTC (1)" in rig.app.screen.text
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(rig.app.screen, WatchScreen))

    async def test_add_says_the_count_and_not_the_address(self, tmp_path):
        rig = Rig(tmp_path)
        rig.app.watching = FakeWatch()
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await run(rig, pilot, f"watch add btc {ADDRESS}")
            await until(pilot, lambda: rig.app.last_message != "")
            assert rig.app.watching.sent == [("add", "BTC", ADDRESS)]
            assert "BTC 1" in rig.app.last_message and ADDRESS not in rig.app.last_message
            assert not rig.app.last_error

    async def test_a_refusal_is_shown_as_an_error(self, tmp_path):
        rig = Rig(tmp_path)
        rig.app.watching = FakeWatch()
        rig.app.watching.refuse = "checksum incorrecto (dirección mal copiada)"
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await run(rig, pilot, f"watch add btc {ADDRESS}")
            await until(pilot, lambda: rig.app.last_error)
            assert "checksum" in rig.app.last_message and ADDRESS not in rig.app.last_message

    async def test_without_a_daemon(self, tmp_path):
        rig = Rig(tmp_path)
        rig.app.watching = None
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await run(rig, pilot, "watch")
            await until(pilot, lambda: rig.app.last_error)
            assert "daemon" in rig.app.last_message

    async def test_bad_syntax_is_a_usage_error(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await run(rig, pilot, "watch add eth x")
            await until(pilot, lambda: rig.app.last_error)
            assert rig.app.last_message.startswith("uso: watch")
