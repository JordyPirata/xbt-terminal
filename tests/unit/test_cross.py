"""Real BTCB2/BTC prices read from the public APIs on 2026-10-20:

NonKYC  bid 0.00727748  ask 0.00730299
Neoxa   bid 0.00735714  ask 0.00742999
"""

import pytest

from xbt_terminal.core.cross import Quote, cross, sats, venue_stats
from xbt_terminal.core.models import D

NK = Quote("nk", D("0.00727748"), D("0.00730299"), taker_fee=D("0.002"))
NX = Quote("nx", D("0.00735714"), D("0.00742999"), taker_fee=D("0.002"))


def no_fees(quote: Quote) -> Quote:
    return Quote(quote.venue, quote.best_bid, quote.best_ask, taker_fee=D("0"))


@pytest.mark.spec("RF-04")
class TestPerVenue:
    def test_mid_and_spread(self):
        stats = venue_stats(NK)
        assert stats.mid == D("0.007290235")
        assert stats.spread == D("0.00002551")
        assert stats.spread_pct == D("0.00002551") / D("0.007290235") * 100

    @pytest.mark.parametrize(
        "bid,ask",
        [
            (None, D("0.0073")),
            (D("0.0073"), None),
            (D("0.0074"), D("0.0073")),
            (D("0.0073"), D("0.0073")),
        ],
    )
    def test_no_reliable_price_no_stats(self, bid, ask):
        assert venue_stats(Quote("nk", bid, ask, taker_fee=D("0"))) is None


@pytest.mark.spec("RF-04")
class TestBetweenVenues:
    def test_mid_difference(self):
        view = cross(NK, NX)
        assert view.mid_diff == D("0.00010333")  # mid_nx - mid_nk
        assert view.mid_diff_pct == D("0.00010333") / D("0.007290235") * 100

    def test_buy_on_nk_sell_on_nx(self):
        route = cross(NK, NX).route("nk", "nx")
        assert route.gross == D("0.00005415")  # 0.00735714 - 0.00730299
        # 0.00735714 × 0.998 - 0.00730299 × 1.002 = 0.00734242572 - 0.00731759598
        assert route.net == D("0.00002482974")
        assert route.gross_pct == D("0.00005415") / D("0.00730299") * 100
        assert route.net_pct == D("0.00002482974") / D("0.00730299") * 100

    def test_other_direction_is_negative(self):
        route = cross(NK, NX).route("nx", "nk")
        assert route.gross == D("-0.00015251")  # 0.00727748 - 0.00742999
        assert route.net < route.gross

    def test_no_fees_net_equals_gross(self):
        view = cross(no_fees(NK), no_fees(NX))
        for route in view.routes:
            assert route.net == route.gross and route.net_pct == route.gross_pct

    def test_fees_of_each_venue_apart(self):
        solo_nx = cross(no_fees(NK), NX).route("nk", "nx")
        assert solo_nx.net == D("0.00735714") * D("0.998") - D("0.00730299")

    def test_side_missing(self):
        sin_ask_nk = Quote("nk", NK.best_bid, None, taker_fee=NK.taker_fee)
        view = cross(sin_ask_nk, NX)
        assert view.route("nk", "nx") is None  # no ask on nk, so it cannot buy there
        assert view.route("nx", "nk") is not None  # selling on nk only needs its bid
        assert view.stats["nk"] is None and view.mid_diff is None

    def test_same_venue_is_error(self):
        with pytest.raises(ValueError):
            cross(NK, NK)


@pytest.mark.spec("RF-04")
def test_satoshis():
    assert sats(D("0.00005415")) == D("5415")
    assert sats(D("0.00002482974")) == D("2482.974")
    assert sats(D("-0.00015251")) == D("-15251")
