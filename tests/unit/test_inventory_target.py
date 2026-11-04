"""Inventory ratio per venue (T-41, RF-26, CA-12, D-46): % of the value in BTCB2, target
band and the bot's veto of routes worsening a venue."""

from dataclasses import replace

import pytest

from xbt_terminal.core.bot import Fire, Skip, decide, inventory_veto
from xbt_terminal.core.inventory import InventoryTarget, base_pct, inventory
from xbt_terminal.core.models import Balance, D

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
        assert TARGET.worsens(D("25"), D("40"))  # leaves the band
        assert not TARGET.worsens(D("25"), D("34"))  # moves away, but stays inside
        assert not TARGET.worsens(D("5"), D("10"))  # outside, but gets closer
        assert TARGET.worsens(D("10"), D("5"))  # outside and even further
        assert TARGET.worsens(D("20"), D("50"))  # crosses to the other side, further
        assert not TARGET.worsens(D("50"), D("1"))  # crosses to the other side, closer

    def test_porcentaje_de_btcb2(self):
        # 1 BTC + 100 BTCB2 at 0.01 → 1 of 2 BTC in BTCB2
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
    """Total BTC and BTCB2 and mid of each venue (NonKYC at ~26 % by default)."""
    out = dict(views)
    for vid, (q, b) in (("nk", nk), ("nx", nx)):
        v = out[vid]
        mid = (v.bids[0].price + v.asks[0].price) / 2 if mids else None
        out[vid] = replace(v, quote_total=D(q), base_total=D(b), mid=mid)
    return out


@pytest.mark.spec("RF-26", "CA-12")
class TestBot:
    """`crossed()`: buy 50 BTCB2 on NonKYC at 0.007014 and sell on Neoxa."""

    def test_sin_objetivo_dispara(self):
        assert isinstance(decide(totals(crossed()), ARMED, LIMITS, NOW), Fire)

    def test_ruta_que_saca_un_venue_de_la_banda_no_dispara(self):
        # NonKYC: 50 of 1.35 BTC in BTCB2 (~26 %); after buying 50, ~52 %
        result = decide(totals(crossed()), ARMED, LIMITS, NOW, target=TARGET)
        assert isinstance(result, Skip)
        assert result.reason == "inventory_band"
        assert "nk" in result.detail

    def test_ruta_que_acerca_un_venue_que_ya_estaba_fuera_dispara(self):
        # NonKYC at ~1 % (as on 2026-10-22): buying brings it closer to 25 %, though still
        # outside; Neoxa at ~88 %: selling brings it closer too
        views = totals(crossed(), nk=("30", "50"))
        assert isinstance(decide(views, ARMED, LIMITS, NOW, target=TARGET), Fire)

    def test_dentro_de_la_banda_dispara(self):
        # NonKYC 6 BTC + 200 BTCB2 (~19 %) → after buying 50, ~23 %
        views = totals(crossed(), nk=("6", "200"), nx=("4", "200"))
        fire = decide(views, ARMED, LIMITS, NOW, target=TARGET)
        assert isinstance(fire, Fire)

    def test_venta_que_vacia_el_otro_venue_tampoco(self):
        # Neoxa at 25 %; selling 50 of its 60 BTCB2 leaves it far below
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
        # NonKYC: buying 50 at 0.007 with 0.2 % fee costs 0.3507 BTC, not 0.35; with exactly
        # 0.3507 BTC it runs out of BTC (100 % in BTCB2), outside and worse
        views = totals(crossed(), nk=("0.3507", "50"))
        veto = inventory_veto(views["nk"], views["nx"], self.FIRE, TARGET)
        assert veto is not None and "100" in veto
