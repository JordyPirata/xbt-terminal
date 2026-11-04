"""Rebalance plan (T-45, RF-29, CA-14): a BTCB2 or BTC transfer leaving both venues with
the same BTCB2 %, with the fee deducted from what is sent, its cost and whether it is
viable."""

from decimal import Decimal

import pytest

from xbt_terminal.core.models import Balance, D, WithdrawalFee
from xbt_terminal.core.rebalance import Holding, break_even, holdings, plan

SAT = D("0.00000001")

# as on 2026-10-22: NonKYC almost all in BTCB2, Neoxa almost all in BTC
NK = Holding(btc=D("0.001"), btcb2=D("0.3"), btc_free=D("0.001"), btcb2_free=D("0.3"),
             price=D("0.0055"))  # fmt: skip
NX = Holding(btc=D("0.004"), btcb2=D("0.01"), btc_free=D("0.004"), btcb2_free=D("0.01"),
             price=D("0.0057"))  # fmt: skip


def fee(venue, asset, amount, minimum=None, active=True, on_top=None):
    """Like the venues: Neoxa charges the fee on top; NonKYC, deducted (D-53)."""
    ticker = {"BTC": "BTC-MAIN", "BTCB2": "BTCB2"}[asset] if venue == "nk" else asset.lower()
    on_top = venue == "nx" if on_top is None else on_top
    return WithdrawalFee(
        venue, asset, ticker, D(amount), D(minimum) if minimum else None, active, on_top
    )


FEES = {
    ("nk", "BTC"): fee("nk", "BTC", "0.00005", "0.00001"),
    ("nk", "BTCB2"): fee("nk", "BTCB2", "0.0027"),
    ("nx", "BTC"): fee("nx", "BTC", "0.0001", "0.0005"),
    ("nx", "BTCB2"): fee("nx", "BTCB2", "0.0001", "0.01"),
}


def ratio(btc: Decimal, btcb2: Decimal, price: Decimal) -> Decimal:
    """BTCB2 value over BTC: equal on both venues ⇔ same %."""
    return btcb2 * price / btc


def by_asset(p, asset):
    return next(t for t in p.transfers if t.asset == asset)


@pytest.mark.spec("RF-29", "CA-14")
class TestQuantity:
    def test_btcb2_from_higher_percent_venue_equalizes_to_the_satoshi(self):
        t = by_asset(plan({"nk": NK, "nx": NX}, FEES), "BTCB2")
        assert (t.source, t.dest) == ("nk", "nx")
        # NonKYC: fee deducted (assumption), the amount asked leaves
        assert t.fee == D("0.0027") and t.received == t.amount - t.fee and t.debited == t.amount
        assert t.amount == t.amount.quantize(SAT)

        def gap(x):  # nk minus nx after sending x: changes sign at the right quantity
            a = ratio(NK.btc, NK.btcb2 - x, NK.price)
            b = ratio(NX.btc, NX.btcb2 + x - t.fee, NX.price)
            return a - b

        assert gap(t.amount) >= 0 > gap(t.amount + SAT)
        assert abs(t.after["nk"] - t.after["nx"]) < D("0.001")

    def test_btc_from_lower_percent_venue_equalizes_to_the_satoshi(self):
        t = by_asset(plan({"nk": NK, "nx": NX}, FEES), "BTC")
        assert (t.source, t.dest) == ("nx", "nk")
        # Neoxa: the fee is on top, the amount asked arrives
        assert t.received == t.amount and t.debited == t.amount + D("0.0001")

        def gap(y):
            a = ratio(NK.btc + y, NK.btcb2, NK.price)
            b = ratio(NX.btc - y - t.fee, NX.btcb2, NX.price)
            return a - b

        assert gap(t.amount) >= 0 > gap(t.amount + SAT)
        assert abs(t.after["nk"] - t.after["nx"]) < D("0.01")  # 1 sat of BTC weighs more

    def test_origin_is_the_higher_percent_even_if_order_changes(self):
        p = plan({"nk": NX, "nx": NK}, {(("nx" if v == "nk" else "nk"), a): f
                                         for (v, a), f in FEES.items()})  # fmt: skip
        assert (by_asset(p, "BTCB2").source, by_asset(p, "BTC").source) == ("nx", "nk")

    def test_percentages_before_and_total(self):
        p = plan({"nk": NK, "nx": NX}, FEES)
        assert round(p.before["nk"], 2) == D("62.26")
        assert round(p.before["nx"], 2) == D("1.40")
        total = (D("0.3") * D("0.0055") + D("0.01") * D("0.0057")) / (
            D("0.005") + D("0.3") * D("0.0055") + D("0.01") * D("0.0057")
        ) * 100  # fmt: skip
        assert p.total_pct == total

    def test_equal_needs_nothing(self):
        p = plan({"nk": NK, "nx": NK}, FEES)
        assert all(not t.viable and "no hace falta" in t.problem for t in p.transfers)
        assert p.best is None


@pytest.mark.spec("RF-29", "CA-14")
class TestCost:
    def test_cost_in_btc_at_origin_mid(self):
        p = plan({"nk": NK, "nx": NX}, FEES)
        assert by_asset(p, "BTCB2").cost_btc == D("0.0027") * NK.price
        assert by_asset(p, "BTC").cost_btc == D("0.0001")

    def test_recommends_cheapest_viable(self):
        p = plan({"nk": NK, "nx": NX}, FEES)
        assert p.best is by_asset(p, "BTCB2")  # 1 485 sats against 10 000

    def test_if_cheap_one_not_viable_recommends_other(self):
        fees = {**FEES, ("nk", "BTCB2"): fee("nk", "BTCB2", "0.0027", active=False)}
        p = plan({"nk": NK, "nx": NX}, fees)
        assert p.best is by_asset(p, "BTC")

    def test_break_even(self):
        # 0.05 BTCB2 at 0.0055 with 0.5 %: 0.000001375 BTC per double
        assert break_even(D("0.00001485"), D("0.05"), D("0.0055"), D("0.5")) == 11
        assert break_even(D("0.000001375"), D("0.05"), D("0.0055"), D("0.5")) == 1
        assert break_even(D("0.0001"), D("0.05"), D("0.0055"), D("0")) is None


@pytest.mark.spec("RF-29", "CA-14")
class TestNotViable:
    def test_exceeds_available(self):
        busy = Holding(NK.btc, NK.btcb2, NK.btc_free, D("0.05"), NK.price)  # the rest in orders
        t = by_asset(plan({"nk": busy, "nx": NX}, FEES), "BTCB2")
        assert not t.viable and "disponible" in t.problem and "0.05" in t.problem

    def test_below_minimum(self):
        fees = {**FEES, ("nx", "BTC"): fee("nx", "BTC", "0.0001", "0.01")}
        t = by_asset(plan({"nk": NK, "nx": NX}, fees), "BTC")
        assert not t.viable and "mínimo" in t.problem and "0.01" in t.problem

    def test_fee_eats_the_transfer(self):
        # nearly equal: ~0.00185 BTCB2 would be needed and the fee is 0.0027
        close = Holding(NK.btc, D("0.299"), NK.btc_free, D("0.299"), NK.price)
        t = by_asset(plan({"nk": NK, "nx": close}, FEES), "BTCB2")
        assert not t.viable and "comisión" in t.problem

    def test_withdrawal_closed(self):
        fees = {**FEES, ("nx", "BTC"): fee("nx", "BTC", "0.0001", active=False)}
        t = by_asset(plan({"nk": NK, "nx": NX}, fees), "BTC")
        assert not t.viable and "cerrado" in t.problem

    def test_missing_fee_computes_with_zero_and_not_viable(self):
        fees = {**FEES, ("nk", "BTCB2"): "NonKYC: HTTP 500"}
        t = by_asset(plan({"nk": NK, "nx": NX}, fees), "BTCB2")
        assert not t.viable and "HTTP 500" in t.problem
        assert t.fee is None and t.cost_btc is None and t.received == t.amount > 0

    def test_no_btc_on_any_venue(self):
        empty = {v: Holding(D(0), h.btcb2, D(0), h.btcb2_free, h.price)
                 for v, h in (("nk", NK), ("nx", NX))}  # fmt: skip
        p = plan(empty, FEES)
        assert p.best is None and all(not t.viable for t in p.transfers)


BAL = {
    "nk": {"BTC": Balance("BTC", D("0.001")), "BTCB2": Balance("BTCB2", D("0.2"), D("0.1"))},
    "nx": {"BTC": Balance("BTC", D("0.003"), D("0.001")), "BTCB2": Balance("BTCB2", D("0.01"))},
}


@pytest.mark.spec("RF-29")
class TestHoldings:
    def test_totals_for_percent_and_available_for_sending(self):
        got = holdings(BAL, {"nk": D("0.0055"), "nx": D("0.0057")})
        assert got["nk"] == Holding(D("0.001"), D("0.3"), D("0.001"), D("0.2"), D("0.0055"))
        assert got["nx"] == Holding(D("0.004"), D("0.01"), D("0.003"), D("0.01"), D("0.0057"))

    def test_no_balances_or_mid_says_so(self):
        assert "nx" in holdings({**BAL, "nx": None}, {"nk": D("1"), "nx": D("1")})
        assert "nk" in holdings(BAL, {"nk": None, "nx": D("1")})

    def test_both_venues_needed(self):
        assert isinstance(plan({"nk": NK}, FEES), str)


@pytest.mark.spec("RF-29", "CA-14")
class TestFeeOnTop:
    """As on 2026-10-23: Neoxa with the BTCB2 and NonKYC without. Neoxa's web asks 0.2, 0.2
    arrives and 0.2001 is deducted (D-53)."""

    NX_FULL = Holding(D("0.00345193"), D("0.33280677"), D("0.00345193"), D("0.33280677"),
                      D("0.004061315"))  # fmt: skip
    NK_EMPTY = Holding(D("0.00261223"), D("0.00000708"), D("0.00261223"), D("0.00000708"),
                       D("0.00422892"))  # fmt: skip

    def test_neoxa_btcb2_arrives_as_asked_and_leaves_with_the_fee(self):
        t = by_asset(plan({"nk": self.NK_EMPTY, "nx": self.NX_FULL}, FEES), "BTCB2")
        assert (t.source, t.dest) == ("nx", "nk")
        assert t.amount == D("0.14002494")  # the recomputation done by hand that day
        assert t.received == t.amount and t.debited == t.amount + D("0.0001")
        assert round(t.after["nx"], 2) == round(t.after["nk"], 2) == D("18.48")

    def test_available_counts_the_fee(self):
        # with just enough for the amount but not the fee, it is not viable
        need = by_asset(plan({"nk": self.NK_EMPTY, "nx": self.NX_FULL}, FEES), "BTCB2").amount
        tight = Holding(self.NX_FULL.btc, self.NX_FULL.btcb2, self.NX_FULL.btc_free, need,
                        self.NX_FULL.price)  # fmt: skip
        t = by_asset(plan({"nk": self.NK_EMPTY, "nx": tight}, FEES), "BTCB2")
        assert not t.viable and "disponible" in t.problem

    def test_on_top_fee_above_the_difference(self):
        fees = {**FEES, ("nk", "BTCB2"): fee("nk", "BTCB2", "0.0027", on_top=True)}
        close = Holding(NK.btc, D("0.299"), NK.btc_free, D("0.299"), NK.price)
        t = by_asset(plan({"nk": NK, "nx": close}, fees), "BTCB2")
        # the fee alone is past the middle: no amount equalizes
        assert t.amount == 0 and not t.viable and "mayor que lo que hay que mover" in t.problem
