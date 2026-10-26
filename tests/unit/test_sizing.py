"""Cantidad a partir de un porcentaje del saldo del venue (RF-17, D-21).
Mercado de prueba de NonKYC: lote 0.0001, fee taker 0.2 %."""

import pytest

from btcb2_terminal.core.models import Balance, D
from btcb2_terminal.core.sizing import size_from_percent

from ..fakes import spec

PRICE = D("0.00725")
BAL = {
    "BTC": Balance("BTC", D("0.004"), D("0.001")),  # total 0.005
    "BTCB2": Balance("BTCB2", D("0.4"), D("0.1")),  # total 0.5
}


@pytest.mark.spec("RF-17")
class TestCompra:
    def test_porcentaje_del_btc_total_con_la_fee_descontada(self):
        s = size_from_percent("buy", D("50"), PRICE, BAL, spec("nk"))
        assert (s.basis, s.asset) == (D("0.005"), "BTC")
        assert s.qty == D("0.3441")  # 0.0025 / (0.00725 × 1.002) = 0.34413…

    def test_100_por_ciento_cabe_con_la_fee(self):
        s = size_from_percent("buy", D("100"), PRICE, BAL, spec("nk"))
        assert s.qty == D("0.6882")
        assert s.qty * PRICE * D("1.002") <= D("0.005")
        assert (s.qty + D("0.0001")) * PRICE * D("1.002") > D("0.005")  # el máximo

    def test_lote_fino_de_neoxa(self):
        s = size_from_percent("buy", D("100"), PRICE, BAL, spec("nx"))
        assert s.qty == D("0.68827861")  # 0.005 / 0.0072645 = 0.688278615…


@pytest.mark.spec("RF-17")
class TestVenta:
    def test_porcentaje_del_btcb2_total(self):
        s = size_from_percent("sell", D("25"), PRICE, BAL, spec("nk"))
        assert (s.qty, s.basis, s.asset) == (D("0.125"), D("0.5"), "BTCB2")

    def test_hacia_abajo_al_lote(self):
        assert size_from_percent("sell", D("33.33"), PRICE, BAL, spec("nk")).qty == D("0.1666")

    def test_no_depende_del_precio(self):
        a = size_from_percent("sell", D("10"), D("0.001"), BAL, spec("nk"))
        b = size_from_percent("sell", D("10"), D("0.1"), BAL, spec("nk"))
        assert a.qty == b.qty == D("0.05")


@pytest.mark.spec("RF-17")
class TestSinSaldo:
    @pytest.mark.parametrize("side", ["buy", "sell"])
    def test_sin_el_activo_da_cero(self, side):
        s = size_from_percent(side, D("100"), PRICE, {}, spec("nk"))
        assert s.qty == 0 and s.basis == 0

    def test_menos_de_un_lote_da_cero(self):
        bal = {"BTCB2": Balance("BTCB2", D("0.00009"))}
        assert size_from_percent("sell", D("100"), PRICE, bal, spec("nk")).qty == 0

    def test_cero_conserva_el_exponente_del_lote(self):
        """Sin -0 ni 0E-8 raros: la cantidad es un múltiplo del lote."""
        s = size_from_percent("sell", D("1"), PRICE, {}, spec("nk"))
        assert s.qty % spec("nk").lot_size == 0
