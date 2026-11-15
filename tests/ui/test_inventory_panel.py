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


# -- self-custody (T-104, RF-39) --------------------------------------------------------

MINUTE = 60_000


def render_with(custody, has_custody=True, now=0):
    from xbt_terminal.core.inventory import inventory as total_inventory

    views = {venue_id: AccountView(balances, 0, None) for venue_id, balances in BAL.items()}
    inv = total_inventory(
        BAL, {"nk": D("0.01"), "nx": D("0.01")}, custody, has_custody=has_custody
    )
    return render_balances(views, inv, now, TARGET, custody, has_custody)


@pytest.mark.spec("RF-39", "RF-27", "CA-22")
class TestWalletLine:
    def test_wallet_line_and_total_with_it(self):
        from xbt_terminal.core.inventory import CustodyBalance

        title, text = render_with(CustodyBalance(D("8"), D("0"), 0), now=5 * MINUTE)
        wallet = text.plain.splitlines()[2]
        assert wallet.startswith(" wallet ")
        assert "BTC 8.00000000 · XBT 0.00000000 · valor 8.00000000 BTC · hace 5m" in wallet
        # venues hold 4 BTC and 400 XBT (4 BTC): with 8 BTC more, XBT is 25 % of 16
        assert "BTC 12.00000000 · XBT 400.00000000 · valor 16.00000000 BTC" in title
        assert "XBT 25.0 % (objetivo 25 ±10)" in title

    def test_venue_lines_keep_their_own_percentage(self):
        from xbt_terminal.core.inventory import CustodyBalance

        _, text = render_with(CustodyBalance(D("8"), D("0"), 0))
        assert "XBT 25.0 %" in text.plain and "XBT 75.0 %" in text.plain

    def test_missing_scan_hides_the_total(self):
        title, text = render_with(None)
        assert "sin dato del explorador" in text.plain.splitlines()[2]
        assert "yellow" in style_of(text, "sin dato del explorador")
        assert title == "Balances · total — (falta la wallet)"

    def test_without_wallets_the_panel_is_as_before(self):
        assert render_with(None, has_custody=False) == render(TARGET)
        assert len(render(TARGET)[1].plain.splitlines()) == 2


@pytest.mark.spec("RF-39", "RF-31", "CA-22")
async def test_screen_draws_the_wallet_the_daemon_reports(tmp_path):
    from xbt_terminal.core.inventory import CustodyBalance
    from xbt_terminal.services.account import AccountService
    from xbt_terminal.ui.panels import BalancesPanel

    from .test_orders_ui import Rig, ready

    rig = Rig(tmp_path)
    account = AccountService({"nk": rig.venue})
    rig.app.account = account
    async with rig.app.run_test(size=(160, 45)) as pilot:
        await ready(rig, pilot)
        account.state["nk"].balances = BAL["nk"]
        rig.app._draw_balances()
        panel = rig.app.query_one(BalancesPanel)
        assert "wallet" not in panel.text
        account.has_custody = True  # what `RemoteAccount` carries from the snapshot
        account.custody = CustodyBalance(D("0.5"), D("12"), rig.app._clock())
        rig.app._draw_balances()
        assert " wallet BTC 0.50000000 · XBT 12.00000000" in panel.text
        assert rig.app.total_with_custody() == rig.app._inventory().base_pct
        account.custody = None
        rig.app._draw_balances()
        assert "sin dato del explorador" in panel.text
        assert rig.app.total_with_custody() is None
