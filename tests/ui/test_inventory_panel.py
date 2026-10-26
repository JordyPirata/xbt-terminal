"""% de BTCB2 por venue y total en el panel de balances (T-41, RF-27, CA-12)."""

import pytest

from btcb2_terminal.core.inventory import InventoryTarget, inventory
from btcb2_terminal.core.models import Balance, D
from btcb2_terminal.ui.panels import AccountView, render_balances

TARGET = InventoryTarget(D("25"), D("10"))
BAL = {
    "nk": {"BTC": Balance("BTC", D("3")), "BTCB2": Balance("BTCB2", D("100"))},
    "nx": {"BTC": Balance("BTC", D("1")), "BTCB2": Balance("BTCB2", D("300"))},
}


def render(target):
    views = {vid: AccountView(b, 0, None) for vid, b in BAL.items()}
    inv = inventory(BAL, {"nk": D("0.01"), "nx": D("0.01")})
    return render_balances(views, inv, 0, target)


def style_of(text, fragment):
    start = text.plain.index(fragment)
    return [s.style for s in text.spans if s.start <= start < s.end]


@pytest.mark.spec("RF-27", "CA-12")
class TestPanel:
    def test_porcentajes_con_color(self):
        title, text = render(TARGET)
        assert "XBT 25.0 %" in text.plain and "XBT 75.0 %" in text.plain
        assert "green" in style_of(text, "XBT 25.0 %")
        assert "yellow" in style_of(text, "XBT 75.0 %")
        assert "XBT 50.0 % (objetivo 25 ±10)" in title

    def test_sin_objetivo_sin_color(self):
        title, text = render(None)
        assert "XBT 25.0 %" in text.plain
        assert style_of(text, "XBT 25.0 %") == []
        assert "XBT 50.0 %" in title and "objetivo" not in title
