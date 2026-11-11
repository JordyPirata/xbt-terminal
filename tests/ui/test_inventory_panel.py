"""BTCB2 % per venue and total in the balance panel (T-41, RF-27, CA-12)."""

import pytest

from xbt_terminal.core.inventory import InventoryTarget, inventory
from xbt_terminal.core.models import Balance, D
from xbt_terminal.ui.panels import AccountView, render_balances

TARGET = InventoryTarget(D("25"), D("10"))
BAL = {
    "nk": {"BTC": Balance("BTC", D("3")), "BTCB2": Balance("BTCB2", D("100"))},
    "nx": {"BTC": Balance("BTC", D("1")), "BTCB2": Balance("BTCB2", D("300"))},
}


def render(target):
    views = {venue_id: AccountView(balances, 0, None) for venue_id, balances in BAL.items()}
    inv = inventory(BAL, {"nk": D("0.01"), "nx": D("0.01")})
    return render_balances(views, inv, 0, target)


def style_of(text, fragment):
    start = text.plain.index(fragment)
    return [span.style for span in text.spans if span.start <= start < span.end]


@pytest.mark.spec("RF-27", "CA-12")
class TestPanel:
    def test_percentages_with_colour(self):
        title, text = render(TARGET)
        assert "XBT 25.0 %" in text.plain and "XBT 75.0 %" in text.plain
        assert "green" in style_of(text, "XBT 25.0 %")
        assert "yellow" in style_of(text, "XBT 75.0 %")
        assert "XBT 50.0 % (objetivo 25 ±10)" in title

    def test_no_target_no_colour(self):
        title, text = render(None)
        assert "XBT 25.0 %" in text.plain
        assert style_of(text, "XBT 25.0 %") == []
        assert "XBT 50.0 %" in title and "objetivo" not in title
