"""Quantity from a percentage of the venue balance (RF-17, D-21).
NonKYC test market: lot 0.0001, taker fee 0.2 %."""

import pytest

from xbt_terminal.core.models import Balance, D
from xbt_terminal.core.sizing import size_from_percent

from ..fakes import spec

PRICE = D("0.00725")
BAL = {
    "BTC": Balance("BTC", D("0.004"), D("0.001")),  # total 0.005
    "BTCB2": Balance("BTCB2", D("0.4"), D("0.1")),  # total 0.5
}


@pytest.mark.spec("RF-17")
class TestBuy:
    def test_percent_of_total_btc_minus_fee(self):
        s = size_from_percent("buy", D("50"), PRICE, BAL, spec("nk"))
        assert (s.basis, s.asset) == (D("0.005"), "BTC")
        assert s.qty == D("0.3441")  # 0.0025 / (0.00725 × 1.002) = 0.34413…

    def test_full_100_percent_fits_with_the_fee(self):
        s = size_from_percent("buy", D("100"), PRICE, BAL, spec("nk"))
        assert s.qty == D("0.6882")
        assert s.qty * PRICE * D("1.002") <= D("0.005")
        assert (s.qty + D("0.0001")) * PRICE * D("1.002") > D("0.005")  # the maximum

    def test_neoxa_fine_lot(self):
        s = size_from_percent("buy", D("100"), PRICE, BAL, spec("nx"))
        assert s.qty == D("0.68827861")  # 0.005 / 0.0072645 = 0.688278615…


@pytest.mark.spec("RF-17")
class TestSell:
    def test_percent_of_total_btcb2(self):
        s = size_from_percent("sell", D("25"), PRICE, BAL, spec("nk"))
        assert (s.qty, s.basis, s.asset) == (D("0.125"), D("0.5"), "BTCB2")

    def test_down_to_the_lot(self):
        assert size_from_percent("sell", D("33.33"), PRICE, BAL, spec("nk")).qty == D("0.1666")

    def test_independent_of_price(self):
        a = size_from_percent("sell", D("10"), D("0.001"), BAL, spec("nk"))
        b = size_from_percent("sell", D("10"), D("0.1"), BAL, spec("nk"))
        assert a.qty == b.qty == D("0.05")


@pytest.mark.spec("RF-17")
class TestNoBalance:
    @pytest.mark.parametrize("side", ["buy", "sell"])
    def test_without_asset_gives_zero(self, side):
        s = size_from_percent(side, D("100"), PRICE, {}, spec("nk"))
        assert s.qty == 0 and s.basis == 0

    def test_less_than_a_lot_gives_zero(self):
        bal = {"BTCB2": Balance("BTCB2", D("0.00009"))}
        assert size_from_percent("sell", D("100"), PRICE, bal, spec("nk")).qty == 0

    def test_zero_keeps_the_lot_exponent(self):
        """No odd -0 or 0E-8: the quantity is a multiple of the lot."""
        s = size_from_percent("sell", D("1"), PRICE, {}, spec("nk"))
        assert s.qty % spec("nk").lot_size == 0
