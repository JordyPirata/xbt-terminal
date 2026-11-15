"""Self-custody in the total inventory (T-104, RF-39, CA-22, D-84): the wallets join the
total and its %, never a venue, the order cap or the bot."""

import inspect

import pytest

from xbt_terminal.core import bot as core_bot
from xbt_terminal.core.inventory import CustodyBalance, inventory
from xbt_terminal.core.models import Balance, D
from xbt_terminal.services import bot as bot_service
from xbt_terminal.services import orders as order_service

BAL = {
    "nk": {"BTC": Balance("BTC", D("3")), "BTCB2": Balance("BTCB2", D("100"), D("50"))},
    "nx": {"BTC": Balance("BTC", D("1")), "BTCB2": Balance("BTCB2", D("300"))},
}
MIDS = {"nk": D("0.01"), "nx": D("0.02")}
WALLET = CustodyBalance(D("2"), D("1000"), 0)


@pytest.mark.spec("RF-39", "CA-22")
class TestTotals:
    def test_wallet_joins_the_totals(self):
        plain = inventory(BAL, MIDS)
        got = inventory(BAL, MIDS, WALLET, has_custody=True)
        assert got.btc == plain.btc + 2 == D("6")
        assert got.btcb2 == plain.btcb2 + 1000 == D("1450")
        assert got.custody_mid == D("0.015")  # mean of the venue mids
        assert got.custody.value == D("2") + D("1000") * D("0.015") == D("17")
        assert got.value == plain.value + D("17")
        assert got.base_pct == (got.value - got.btc) / got.value * 100

    def test_venues_do_not_change(self):
        assert inventory(BAL, MIDS, WALLET, has_custody=True).venues == inventory(BAL, MIDS).venues

    def test_without_wallets_nothing_changes(self):
        got = inventory(BAL, MIDS)
        assert (got.custody, got.custody_mid) == (None, None)
        assert got == inventory(BAL, MIDS, None, has_custody=False)

    def test_configured_but_missing_there_is_no_total(self):
        got = inventory(BAL, MIDS, None, has_custody=True)
        assert (got.btc, got.btcb2, got.value, got.base_pct) == (None, None, None, None)
        assert got.venues == inventory(BAL, MIDS).venues

    def test_without_a_venue_mid_the_wallet_has_no_value(self):
        got = inventory(BAL, {"nk": D("0.01"), "nx": None}, WALLET, has_custody=True)
        assert got.custody.value is None and got.value is None
        assert (got.btc, got.btcb2) == (D("6"), D("1450"))

    def test_missing_venue_balance_still_hides_the_total(self):
        got = inventory({"nk": BAL["nk"], "nx": None}, MIDS, WALLET, has_custody=True)
        assert (got.btc, got.value) == (None, None)

    def test_only_btc_in_the_wallet(self):
        got = inventory(BAL, MIDS, CustodyBalance(D("5"), D("0"), 0), has_custody=True)
        assert got.custody.value == D("5") and got.custody.base_pct == 0


@pytest.mark.spec("RF-39", "RF-10", "RF-26", "CA-22")
class TestOutOfReach:
    """The order cap, the venue exposure and the bot's filter only see the venues: none
    of them can be handed the wallets."""

    @pytest.mark.parametrize("module", [order_service, bot_service, core_bot])
    def test_orders_and_bot_never_mention_custody(self, module):
        assert "custody" not in inspect.getsource(module).lower()

    def test_default_call_is_the_venue_only_inventory(self):
        parameters = inspect.signature(inventory).parameters
        assert parameters["custody"].default is None
        assert parameters["has_custody"].default is False
