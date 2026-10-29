"""Formatos de sats y % de la pantalla (T-81, D-77): un solo sitio para todos."""

import pytest

from xbt_terminal.core.models import D
from xbt_terminal.ui.format import (
    fmt_btc_sats_pct,
    fmt_pct,
    fmt_sats,
    fmt_sats_grouped,
    fmt_share,
)


@pytest.mark.spec("RF-25")
class TestSats:
    def test_con_signo_por_defecto(self):
        assert fmt_sats(D("0.00001234")) == "+1234"
        assert fmt_sats(D("-0.00000005")) == "-5"
        assert fmt_sats(D("0")) == "+0"

    def test_sin_signo(self):
        assert fmt_sats(D("0.00001234"), signed=False) == "1234"

    def test_redondea_a_sats_enteros(self):
        assert fmt_sats(D("0.000000016")) == "+2"

    def test_sin_valor(self):
        assert fmt_sats(None) == "—"


@pytest.mark.spec("RF-29")
def test_sats_con_miles_y_unidad():
    assert fmt_sats_grouped(D("0.01234567")) == "1 234 567 sats"
    assert fmt_sats_grouped(D("0.00000999")) == "999 sats"
    assert fmt_sats_grouped(None) == "—"


@pytest.mark.spec("RF-25", "RF-29")
class TestPorcentaje:
    def test_dos_decimales_con_signo_por_defecto(self):
        assert fmt_pct(D("1.234")) == "+1.23 %"
        assert fmt_pct(D("-0.5")) == "-0.50 %"

    def test_un_decimal_sin_signo(self):
        assert fmt_pct(D("27.66"), decimals=1, signed=False) == "27.7 %"

    def test_sin_valor(self):
        assert fmt_pct(None) == "—"
        assert fmt_pct(None, decimals=1, signed=False) == "—"


@pytest.mark.spec("RF-29")
def test_parte_del_inventario():
    assert fmt_share(D("27.66")) == "27.7 %"
    assert fmt_share(D("-1")) == "-1.0 %"
    assert fmt_share(None) == "—"


@pytest.mark.spec("RF-04")
def test_precio_sats_y_porcentaje():
    assert (
        fmt_btc_sats_pct(D("0.00000123"), D("0.0385"), signed=True)
        == "+0.00000123 · +123 sats · +0.04 %"
    )
    assert (
        fmt_btc_sats_pct(D("0.00000123"), D("0.0385"), signed=False)
        == "0.00000123 · 123 sats · 0.04 %"
    )
