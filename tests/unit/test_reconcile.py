"""Candidates for an uncertain send (T-25, RNF-12, PA-09, D-26)."""

import pytest

from xbt_terminal.core.models import D, Order, OrderRequest
from xbt_terminal.core.reconcile import WINDOW_MS, candidates

SENT = 1_792_530_500_000
REQ = OrderRequest("nx", "buy", D("0.01"), D("0.00725"), "t-abc123")


def order(oid="o-1", *, client_id=None, side="buy", qty="0.01", price="0.00725", ts=SENT,
          venue="nx", status="open"):  # fmt: skip
    return Order(venue, oid, client_id, side, D(price), D(qty), D("0"), status, ts)


@pytest.mark.spec("RNF-12")
class TestByClientId:
    def test_only_same_client_id(self):
        order_request = OrderRequest("nk", "buy", D("0.01"), D("0.00725"), "t-abc123")
        mine = order("a", client_id="t-abc123", venue="nk", ts=SENT - 10 * WINDOW_MS)
        other = order("b", client_id=None, venue="nk")
        assert candidates(order_request, [mine, other], SENT, frozenset(), by_client_id=True) == [
            mine
        ]

    def test_known_at_send_does_not_count(self):
        order_request = OrderRequest("nk", "buy", D("0.01"), D("0.00725"), "t-abc123")
        mine = order("a", client_id="t-abc123", venue="nk")
        assert candidates(order_request, [mine], SENT, frozenset({"a"}), by_client_id=True) == []


@pytest.mark.spec("RNF-12")
class TestWithoutClientId:
    def test_same_side_price_and_quantity_in_window(self):
        same = order("a", qty="0.0100", price="0.007250", ts=SENT + 2_000)
        assert candidates(REQ, [same], SENT, frozenset(), by_client_id=False) == [same]

    @pytest.mark.parametrize(
        "kw",
        [
            {"side": "sell"},
            {"qty": "0.02"},
            {"price": "0.00726"},
            {"venue": "nk"},
            {"ts": SENT + WINDOW_MS + 1},
            {"ts": SENT - WINDOW_MS - 1},
        ],
    )
    def test_mismatch_does_not_count(self, kw):
        assert candidates(REQ, [order(**kw)], SENT, frozenset(), by_client_id=False) == []

    def test_clock_skew_backwards_within_the_window(self):
        early = order("a", ts=SENT - WINDOW_MS)
        assert candidates(REQ, [early], SENT, frozenset(), by_client_id=False) == [early]

    def test_all_candidates_returned(self):
        first, second = order("a"), order("b", status="filled")
        assert candidates(REQ, [first, second], SENT, frozenset(), by_client_id=False) == [
            first,
            second,
        ]

    def test_same_order_twice_counts_once(self):
        """Active and history may bring the same one: the last of the list counts."""
        first, a_later = order("a"), order("a", status="filled", ts=SENT + 1)
        assert candidates(REQ, [first, a_later], SENT, frozenset(), by_client_id=False) == [a_later]
