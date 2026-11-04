"""Net inventory (RF-05, design.md §6.5): BTC and BTCB2 per venue and in total, valued
in BTC at each venue's mid."""

import pytest

from xbt_terminal.core.inventory import inventory
from xbt_terminal.core.models import Balance, D

NK = {"BTC": Balance("BTC", D("0.001"), D("0.0005")), "BTCB2": Balance("BTCB2", D("0.2"), D("0.1"))}
NX = {"BTC": Balance("BTC", D("0.003")), "BTCB2": Balance("BTCB2", D("0"), D("0.05"))}


@pytest.mark.spec("RF-05")
class TestInventory:
    def test_per_venue_and_total(self):
        inv = inventory({"nk": NK, "nx": NX}, {"nk": D("0.0072"), "nx": D("0.0074")})
        nk, nx = inv.venues["nk"], inv.venues["nx"]
        assert (nk.btc, nk.btcb2) == (D("0.0015"), D("0.3"))
        assert nk.value == D("0.0015") + D("0.3") * D("0.0072")
        assert nx.value == D("0.003") + D("0.05") * D("0.0074")
        assert (inv.btc, inv.btcb2) == (D("0.0045"), D("0.35"))
        assert inv.value == nk.value + nx.value

    def test_no_mid_no_value(self):
        inv = inventory({"nk": NK, "nx": NX}, {"nk": D("0.0072"), "nx": None})
        assert inv.venues["nx"].value is None and inv.venues["nk"].value is not None
        assert inv.value is None  # a partial total would mislead
        assert inv.btcb2 == D("0.35")  # quantities do add up

    def test_venue_without_balances_yet(self):
        inv = inventory({"nk": NK, "nx": None}, {"nk": D("0.0072"), "nx": D("0.0074")})
        assert inv.venues["nx"] is None
        assert inv.btc is None and inv.btcb2 is None and inv.value is None

    def test_absent_asset_counts_zero(self):
        inv = inventory({"nk": {"BTC": Balance("BTC", D("1"))}}, {"nk": D("0.007")})
        assert inv.venues["nk"].btcb2 == 0 and inv.value == D("1")

    def test_other_assets_ignored(self):
        """RNF-10: even if an adapter returned USDT, it does not enter the inventory."""
        bal = dict(NK, USDT=Balance("USDT", D("1000")))
        inv = inventory({"nk": bal}, {"nk": D("0.0072")})
        assert inv.btc == D("0.0015")
