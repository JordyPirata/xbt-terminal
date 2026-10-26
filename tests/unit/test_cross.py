"""Precios reales de BTCB2/BTC leídos de las APIs públicas el 2026-10-20:

NonKYC  bid 0.00727748  ask 0.00730299
Neoxa   bid 0.00735714  ask 0.00742999
"""

import pytest

from btcb2_terminal.core.cross import Quote, cross, sats, venue_stats
from btcb2_terminal.core.models import D

NK = Quote("nk", D("0.00727748"), D("0.00730299"), taker_fee=D("0.002"))
NX = Quote("nx", D("0.00735714"), D("0.00742999"), taker_fee=D("0.002"))


def no_fees(q: Quote) -> Quote:
    return Quote(q.venue, q.best_bid, q.best_ask, taker_fee=D("0"))


@pytest.mark.spec("RF-04")
class TestPorVenue:
    def test_mid_y_spread(self):
        s = venue_stats(NK)
        assert s.mid == D("0.007290235")
        assert s.spread == D("0.00002551")
        assert s.spread_pct == D("0.00002551") / D("0.007290235") * 100

    @pytest.mark.parametrize(
        "bid,ask",
        [
            (None, D("0.0073")),
            (D("0.0073"), None),
            (D("0.0074"), D("0.0073")),
            (D("0.0073"), D("0.0073")),
        ],
    )
    def test_sin_precio_fiable_no_hay_stats(self, bid, ask):
        assert venue_stats(Quote("nk", bid, ask, taker_fee=D("0"))) is None


@pytest.mark.spec("RF-04")
class TestEntreVenues:
    def test_diferencia_de_mids(self):
        v = cross(NK, NX)
        assert v.mid_diff == D("0.00010333")  # mid_nx - mid_nk
        assert v.mid_diff_pct == D("0.00010333") / D("0.007290235") * 100

    def test_comprar_en_nk_vender_en_nx(self):
        r = cross(NK, NX).route("nk", "nx")
        assert r.gross == D("0.00005415")  # 0.00735714 - 0.00730299
        # 0.00735714 × 0.998 - 0.00730299 × 1.002 = 0.00734242572 - 0.00731759598
        assert r.net == D("0.00002482974")
        assert r.gross_pct == D("0.00005415") / D("0.00730299") * 100
        assert r.net_pct == D("0.00002482974") / D("0.00730299") * 100

    def test_la_otra_direccion_es_negativa(self):
        r = cross(NK, NX).route("nx", "nk")
        assert r.gross == D("-0.00015251")  # 0.00727748 - 0.00742999
        assert r.net < r.gross

    def test_sin_fees_neto_igual_a_bruto(self):
        v = cross(no_fees(NK), no_fees(NX))
        for r in v.routes:
            assert r.net == r.gross and r.net_pct == r.gross_pct

    def test_fees_de_cada_venue_por_separado(self):
        solo_nx = cross(no_fees(NK), NX).route("nk", "nx")
        assert solo_nx.net == D("0.00735714") * D("0.998") - D("0.00730299")

    def test_falta_un_lado(self):
        sin_ask_nk = Quote("nk", NK.best_bid, None, taker_fee=NK.taker_fee)
        v = cross(sin_ask_nk, NX)
        assert v.route("nk", "nx") is None  # sin ask en nk no se puede comprar ahí
        assert v.route("nx", "nk") is not None  # vender en nk solo necesita su bid
        assert v.stats["nk"] is None and v.mid_diff is None

    def test_mismo_venue_es_error(self):
        with pytest.raises(ValueError):
            cross(NK, NK)


@pytest.mark.spec("RF-04")
def test_satoshis():
    assert sats(D("0.00005415")) == D("5415")
    assert sats(D("0.00002482974")) == D("2482.974")
    assert sats(D("-0.00015251")) == D("-15251")
