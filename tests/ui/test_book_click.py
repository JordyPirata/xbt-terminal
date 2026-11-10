"""Click on the book (T-21, RF-16, D-18): with an empty line the whole order is written,
a buy (click on asks) or a sell (click on bids), with the level's price and quantity,
or the cumulative one if the click is on that column; with text, only the price is
inserted at the cursor.

FakeFeed books: bids 0.00720000 × 1 and 0.00719900 × 2 (cumulative 1 and 3); asks
0.00730000 × 0.5 and 0.00730100 × 3 (cumulative 0.5 and 3.5)."""

import pytest

from xbt_terminal.core.models import BookSnapshot, ConnState, D, Level
from xbt_terminal.services.market import MarketHub
from xbt_terminal.ui.panels import BookPanel, CommandLine

from ..fakes import FakeFeed, book
from .local import local_app
from .test_double_ui import Rig, until

# content columns: bids = cumulative 0-11, quantity 12-23, price 24-34; asks = price
# 40-50, quantity 51-62, cumulative 63-74
BID_X, ASK_X = 30, 45  # price column of each side
BID_CUM_X, ASK_CUM_X = 5, 65


async def ready(rig, pilot):
    """FakeFeed books with two levels per side and the spec loaded."""
    for venue_id in ("nk", "nx"):
        rig.feeds[venue_id].push(
            ConnState(venue_id, "market", "ws", "up", 100.0, rig.now),
            book(venue_id, "0.00720000", "0.00730000", ts=rig.now),
        )
    await until(pilot, lambda: all(rig.hub.state[v].ready for v in ("nk", "nx")))
    panel = rig.app.query_one("#book-nk", BookPanel)
    await until(pilot, lambda: "0.00730000" in panel.text)
    return panel


async def click(pilot, venue, x, y):
    """(x, y) in content coordinates; +1 for the panel border."""
    await pilot.click(f"#book-{venue}", offset=(x + 1, y + 1))
    await pilot.pause()


@pytest.mark.spec("RF-16")
class TestLevelUnderMouse:
    async def test_row_and_column_map(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            panel = await ready(rig, pilot)
            assert panel.level_at(BID_X, 0) is None  # header
            assert panel.level_at(BID_X, 1) == ("bid", D("0.00720000"), D("1"))
            assert panel.level_at(BID_X, 2) == ("bid", D("0.00719900"), D("2"))
            assert panel.level_at(ASK_X, 1) == ("ask", D("0.00730000"), D("0.5"))
            assert panel.level_at(ASK_X, 2) == ("ask", D("0.00730100"), D("3"))
            for x in (37, 38, 39):  # separator " │ "
                assert panel.level_at(x, 1) is None, x
            assert panel.level_at(36, 1) is not None and panel.level_at(40, 1) is not None
            assert panel.level_at(BID_X, 3) is None  # empty level
            assert panel.level_at(BID_X, 16) is None  # summary line

    async def test_cumulative_column_gives_cumulative(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            panel = await ready(rig, pilot)
            assert panel.level_at(BID_CUM_X, 2) == ("bid", D("0.00719900"), D("3"))
            assert panel.level_at(11, 2)[2] == D("3")  # last character of the cumulative
            assert panel.level_at(12, 2)[2] == D("2")  # already the quantity
            assert panel.level_at(ASK_CUM_X, 2) == ("ask", D("0.00730100"), D("3.5"))
            assert panel.level_at(62, 2)[2] == D("3")  # quantity
            assert panel.level_at(63, 2)[2] == D("3.5")  # cumulative
            assert panel.level_at(76, 2)[2] == D("3.5")  # AMM mark: counts as cumulative

    async def test_without_book_no_level(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            assert rig.app.query_one("#book-nk", BookPanel).level_at(BID_X, 1) is None


@pytest.mark.spec("RF-16", "RF-06")
class TestClick:
    async def test_click_on_asks_writes_the_whole_buy(self, tmp_path, monkeypatch):
        rig = Rig(tmp_path)
        copied = []
        monkeypatch.setattr(rig.app, "copy_to_clipboard", copied.append)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nx", ASK_X, 1)
            command = rig.app.query_one(CommandLine)
            assert command.value == "buy nx 0.50000000 0.00730000"
            assert command.has_focus and command.cursor_position == len(command.value)
            assert copied == ["0.00730000"]
            assert "0.00730000" in rig.app.last_message

    async def test_click_on_bids_writes_the_sell(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", BID_X, 2)
            assert rig.app.query_one(CommandLine).value == "sell nk 2.0000 0.00719900"

    async def test_click_on_cumulative_sweeps_to_that_level(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", ASK_CUM_X, 2)
            assert rig.app.query_one(CommandLine).value == "buy nk 3.5000 0.00730100"

    async def test_enter_opens_dialog(self, tmp_path):
        from xbt_terminal.ui.screens.confirm import ConfirmScreen

        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            small = BookSnapshot(
                "nk",
                (Level(D("0.00720000"), D("0.02")),),
                (Level(D("0.00730000"), D("0.01")),),
                rig.now,
            )
            rig.feeds["nk"].push(small)
            panel = rig.app.query_one("#book-nk", BookPanel)
            await until(pilot, lambda: panel.level_at(ASK_X, 1) == ("ask", D("0.0073"), D("0.01")))
            await click(pilot, "nk", ASK_X, 1)
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "BUY 0.0100 XBT @ 0.00730000 en NonKYC" in rig.app.screen.summary

    async def test_quantity_above_cap_not_trimmed(self, tmp_path):
        """Cumulative 3.5 × 0.007301 ≈ 0.0256 BTC > 25 % of the inventory (≈ 0.0043): RiskCheck
        says so, it is not changed behind your back."""
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", ASK_CUM_X, 2)
            assert rig.app.query_one(CommandLine).value == "buy nk 3.5000 0.00730100"
            await pilot.press("enter")
            await until(pilot, lambda: "rechazada" in rig.app.last_message)
            assert "25 % del inventario total" in rig.app.last_message

    async def test_with_text_only_inserts_the_price(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            command = rig.app.query_one(CommandLine)
            command.focus()
            command.value = "dbl 0.01 nk"
            command.cursor_position = len(command.value)
            await click(pilot, "nk", ASK_X, 1)
            assert command.value == "dbl 0.01 nk 0.00730000"
            command.value += " nx"
            command.cursor_position = len(command.value)
            await click(pilot, "nx", BID_X, 1)
            assert command.value == "dbl 0.01 nk 0.00730000 nx 0.00720000"

    async def test_no_double_space(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            command = rig.app.query_one(CommandLine)
            command.focus()
            command.value = "buy nk 0.01 "
            command.cursor_position = len(command.value)
            await click(pilot, "nk", BID_X, 1)
            assert command.value == "buy nk 0.01 0.00720000"

    async def test_click_off_a_level_does_nothing(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", BID_X, 0)  # header
            assert rig.app.query_one(CommandLine).value == ""


@pytest.mark.spec("RF-16", "RF-13")
async def test_read_only_only_copies(monkeypatch):
    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    app = local_app(MarketHub(feeds), book_levels=15, timeframe="1m", mode="read-only")
    copied = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
    async with app.run_test(size=(160, 45)) as pilot:
        feeds["nk"].push(book("nk", "0.00720000", "0.00730000"))
        panel = app.query_one("#book-nk", BookPanel)
        await until(pilot, lambda: "0.00730000" in panel.text)
        await click(pilot, "nk", ASK_X, 1)
        assert app.query_one(CommandLine).value == "" and copied == ["0.00730000"]
        assert "copiado" in app.last_message


@pytest.mark.spec("RF-16", "RF-28")
async def test_click_also_copies_with_system(monkeypatch):
    """D-52: OSC 52 does not reach GNOME Terminal; the price also goes through xclip."""
    copied: list[str] = []

    async def clipboard(text):
        copied.append(text)
        return "xclip"

    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    app = local_app(
        MarketHub(feeds), book_levels=15, timeframe="1m", mode="read-only", clipboard=clipboard
    )
    monkeypatch.setattr(app, "copy_to_clipboard", lambda text: None)
    async with app.run_test(size=(160, 45)) as pilot:
        feeds["nk"].push(book("nk", "0.00720000", "0.00730000"))
        panel = app.query_one("#book-nk", BookPanel)
        await until(pilot, lambda: "0.00730000" in panel.text)
        await click(pilot, "nk", ASK_X, 1)
        await until(pilot, lambda: copied == ["0.00730000"])
