"""Book centred with depth bars on the sides (RF-02, D-49): the ladder sits in the middle
of the panel; left of it, a green bar per bid level growing outwards with the
cumulative, and right of it the asks one. Bars use the spare room: with a tight panel
there are none.

FakeFeed books: bids 0.00720000 × 1 and 0.00719900 × 2 (cumulative 1 and 3); asks
0.00730000 × 0.5 and 0.00730100 × 3 (cumulative 0.5 and 3.5)."""

import pytest

from xbt_terminal.core.models import D
from xbt_terminal.ui.panels import LADDER_W, BookPanel, depth_bar

from .test_book_click import ASK_X, BID_CUM_X, BID_X, ready
from .test_double_ui import Rig

WIDE = (220, 50)  # each book with ~108 content columns


def line_with(panel: BookPanel, needle: str) -> str:
    return next(ln for ln in panel.text.splitlines() if needle in ln)


@pytest.mark.spec("RF-02")
class TestBar:
    def test_bids_grow_left(self):
        assert depth_bar(D(1), 4, "bid") == "████"
        assert depth_bar(D("0.5"), 4, "bid") == "  ██"
        assert depth_bar(D("0.625"), 4, "bid") == " ▐██"

    def test_asks_grow_right(self):
        assert depth_bar(D("0.5"), 4, "ask") == "██  "
        assert depth_bar(D("0.625"), 4, "ask") == "██▌ "

    def test_level_with_something_always_shows(self):
        assert depth_bar(D("0.001"), 4, "ask") == "▌   "
        assert depth_bar(D(0), 4, "ask") == "    "

    def test_no_room_no_bar(self):
        assert depth_bar(D(1), 0, "bid") == ""


@pytest.mark.spec("RF-02", "RF-16")
class TestPanel:
    async def test_side_bars_proportional_to_cumulative(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            first, second = line_with(panel, "0.00720000"), line_with(panel, "0.00719900")
            # bids left of the ladder, asks right
            assert first.index("█") < first.index("0.00720000")
            assert first.rindex("█") > first.index("0.00730000")
            # the deepest level (cumulative 3.5, the maximum) fills its bar
            bar_w = panel.bar_width
            assert bar_w >= 8 and second.rstrip().endswith("█" * bar_w)
            assert second.count("█") > first.count("█")

    async def test_centred_ladder(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            width = panel.content_size.width
            left = panel.ladder_x
            right = width - left - LADDER_W
            assert abs(left - right) <= 1

    async def test_click_shifts_with_the_ladder(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            x0 = panel.ladder_x
            assert x0 > 0
            assert panel.level_at(x0 + BID_X, 2) == ("bid", D("0.00719900"), D("2"))
            assert panel.level_at(x0 + ASK_X, 1) == ("ask", D("0.00730000"), D("0.5"))
            assert panel.level_at(x0 + BID_CUM_X, 2)[2] == D("3")

    async def test_click_on_bar_gives_cumulative(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            x0 = panel.ladder_x
            assert panel.level_at(x0 - 2, 2) == ("bid", D("0.00719900"), D("3"))
            assert panel.level_at(x0 + LADDER_W + 2, 2) == ("ask", D("0.00730100"), D("3.5"))

    async def test_tight_panel_without_bars(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            panel = await ready(rig, pilot)
            assert panel.bar_width == 0 and "█" not in panel.text
