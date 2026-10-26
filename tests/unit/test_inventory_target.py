"""Ratio de inventario por venue (T-41, RF-26, CA-12, D-46): % del valor en
BTCB2, banda objetivo y veto del bot a las rutas que empeoran un venue."""

from dataclasses import replace

import pytest

from btcb2_terminal.core.bot import Fire, Skip, decide, inventory_veto
from btcb2_terminal.core.inventory import InventoryTarget, base_pct, inventory
from btcb2_terminal.core.models import Balance, D

from .test_bot import ARMED, LIMITS, NOW, crossed

TARGET = InventoryTarget(D("25"), D("10"))


@pytest.mark.spec("RF-26")
class TestObjetivo:
    def test_banda(self):
        assert (TARGET.low, TARGET.high) == (D("15"), D("35"))
        assert TARGET.in_band(D("15")) and TARGET.in_band(D("35"))
        assert not TARGET.in_band(D("14.99")) and not TARGET.in_band(D("35.01"))

    def test_banda_recortada_a_0_y_100(self):
        t = InventoryTarget(D("5"), D("10"))
        assert (t.low, t.high) == (D("0"), D("15"))

    def test_empeora_solo_si_queda_fuera_y_mas_lejos(self):
        assert TARGET.worsens(D("25"), D("40"))  # sale de la banda
        assert not TARGET.worsens(D("25"), D("34"))  # se aleja, pero sigue dentro
        assert not TARGET.worsens(D("5"), D("10"))  # fuera, pero se acerca
        assert TARGET.worsens(D("10"), D("5"))  # fuera y aún más lejos
        assert TARGET.worsens(D("20"), D("50"))  # cruza al otro lado más lejos
        assert not TARGET.worsens(D("50"), D("1"))  # cruza al otro lado, más cerca

    def test_porcentaje_de_btcb2(self):
        # 1 BTC + 100 BTCB2 a 0.01 → 1 de 2 BTC en BTCB2
        assert base_pct(D("1"), D("100"), D("0.01")) == D("50")
        assert base_pct(D("0"), D("0"), D("0.01")) is None

    def test_inventario_con_porcentajes(self):
        bal = {
            "nk": {"BTC": Balance("BTC", D("3")), "BTCB2": Balance("BTCB2", D("100"))},
            "nx": {"BTC": Balance("BTC", D("1")), "BTCB2": Balance("BTCB2", D("300"))},
        }
        inv = inventory(bal, {"nk": D("0.01"), "nx": D("0.01")})
        assert inv.venues["nk"].base_pct == D("25")
        assert inv.venues["nx"].base_pct == D("75")
        assert inv.base_pct == D("50")
        assert inventory(bal, {"nk": None, "nx": D("0.01")}).base_pct is None


def totals(views, nk=("1", "50"), nx=("1", "1000"), mids=True):
    """BTC y BTCB2 totales y mid de cada venue (por defecto NonKYC al ~26 %)."""
    out = dict(views)
    for vid, (q, b) in (("nk", nk), ("nx", nx)):
        v = out[vid]
        mid = (v.bids[0].price + v.asks[0].price) / 2 if mids else None
        out[vid] = replace(v, quote_total=D(q), base_total=D(b), mid=mid)
    return out


@pytest.mark.spec("RF-26", "CA-12")
class TestBot:
    """`crossed()`: comprar 50 BTCB2 en NonKYC a 0.007014 y vender en Neoxa."""

    def test_sin_objetivo_dispara(self):
        assert isinstance(decide(totals(crossed()), ARMED, LIMITS, NOW), Fire)

    def test_ruta_que_saca_un_venue_de_la_banda_no_dispara(self):
        # NonKYC: 50 de 1.35 BTC en BTCB2 (~26 %); tras comprar 50, ~52 %
        result = decide(totals(crossed()), ARMED, LIMITS, NOW, target=TARGET)
        assert isinstance(result, Skip)
        assert result.reason == "inventory_band"
        assert "nk" in result.detail

    def test_ruta_que_acerca_un_venue_que_ya_estaba_fuera_dispara(self):
        # NonKYC al ~1 % (como el 2026-10-22): comprar lo acerca al 25 %, aunque
        # siga fuera; Neoxa al ~88 %: vender también lo acerca
        views = totals(crossed(), nk=("30", "50"))
        assert isinstance(decide(views, ARMED, LIMITS, NOW, target=TARGET), Fire)

    def test_dentro_de_la_banda_dispara(self):
        # NonKYC 6 BTC + 200 BTCB2 (~19 %) → tras comprar 50, ~23 %
        views = totals(crossed(), nk=("6", "200"), nx=("4", "200"))
        fire = decide(views, ARMED, LIMITS, NOW, target=TARGET)
        assert isinstance(fire, Fire)

    def test_venta_que_vacia_el_otro_venue_tampoco(self):
        # Neoxa al 25 %; vender 50 de sus 60 BTCB2 lo deja muy por debajo
        views = totals(crossed(), nk=("6", "200"), nx=("1.3", "60"))
        result = decide(views, ARMED, LIMITS, NOW, target=TARGET)
        assert isinstance(result, Skip) and "nx" in result.detail

    def test_venue_vacio_no_se_comprueba(self):
        views = totals(crossed(), nk=("0", "0"), nx=("6", "300"))
        views["nk"] = replace(views["nk"], quote_available=D("1"))
        assert isinstance(decide(views, ARMED, LIMITS, NOW, target=TARGET), Fire)


@pytest.mark.spec("RF-26", "CA-12")
class TestVeto:
    FIRE = Fire("nk", "nx", D("0.007"), D("0.0072"), D("50"), D("0"), D("1"))

    def test_con_fees_y_mid(self):
        views = totals(crossed(), nk=("6", "200"), nx=("4", "200"))
        assert inventory_veto(views["nk"], views["nx"], self.FIRE, TARGET) is None

    def test_sin_mid_valora_al_precio_limite(self):
        views = totals(crossed(), mids=False)
        veto = inventory_veto(views["nk"], views["nx"], self.FIRE, TARGET)
        assert veto is not None and "nk" in veto

    def test_la_fee_cuenta_en_el_btc(self):
        # NonKYC: comprar 50 a 0.007 con fee 0.2 % cuesta 0.3507 BTC, no 0.35;
        # con 0.3507 BTC justos se queda sin BTC (100 % en BTCB2), fuera y peor
        views = totals(crossed(), nk=("0.3507", "50"))
        veto = inventory_veto(views["nk"], views["nx"], self.FIRE, TARGET)
        assert veto is not None and "100" in veto
