"""Realized result of double orders from the journal (T-40, RF-25, CA-11,
design.md §6.10, D-44, D-45).

Synthetic journal: `order_sent` with each leg's `double`, `order_ack` with its
`order_id`, `fill` (with `client_id`, only `order_id` or neither, like Neoxa's),
`bot_fire` and `hanging_leg`."""

import json
from datetime import UTC, datetime

import pytest

from xbt_terminal.core.models import D
from xbt_terminal.core.results import compute, range_start, read_rows

T0 = int(datetime(2026, 10, 22, 12, 0, tzinfo=UTC).timestamp() * 1000)
DAY = 86_400_000


def ts(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def row(event, at, venue=None, client_id=None, mode="live", **data):
    return {
        "ts": ts(at), "mode": mode, "venue": venue, "event": event,
        "client_id": client_id, "data": data,
    }  # fmt: skip


def sent(cid, venue, side, qty, price, double, at=T0, mode="live"):
    return row("order_sent", at, venue, cid, mode, side=side, qty=qty, price=price, double=double)


def ack(cid, venue, oid, at=T0):
    return row("order_ack", at, venue, cid, order_id=oid, status="open")


def fill(venue, side, qty, price, fee="0", asset="BTC", cid=None, oid=None, at=T0 + 1000, tid=None):
    return row(
        "fill", at, venue, cid, trade_id=tid or f"tr-{venue}-{at}-{qty}", order_id=oid,
        side=side, qty=qty, price=price, fee=fee, fee_asset=asset,
    )  # fmt: skip


def fire(double, net_pct, at=T0):
    return row("bot_fire", at, double=double, net_pct=net_pct, net="0", qty="0")


def plain_double(double="d-1", at=T0, qty="0.01", buy="0.0050", sell="0.0052"):
    """Buy on nx and sell on nk, both filled, fee 0."""
    b, s = f"t-{double}-b", f"t-{double}-s"
    return [
        sent(b, "nx", "buy", qty, buy, double, at),
        sent(s, "nk", "sell", qty, sell, double, at),
        fill("nx", "buy", qty, buy, at=at + 1000),
        fill("nk", "sell", qty, sell, cid=s, at=at + 1000),
    ]


@pytest.mark.spec("RF-25", "CA-11")
class TestResult:
    def test_filled_double_without_fees(self):
        rep = compute(plain_double())
        (d,) = rep.doubles
        assert d.status == "closed"
        assert (d.buy_venue, d.sell_venue) == ("nx", "nk")
        assert d.matched == D("0.01")
        assert d.result == D("0.000002")  # 0.01 × (0.0052 - 0.0050)
        assert d.mismatch == 0

    def test_btc_fees_subtract(self):
        rows = [
            sent("t-b", "nk", "buy", "0.01", "0.0050", "d-1"),
            sent("t-s", "nx", "sell", "0.01", "0.0052", "d-1"),
            fill("nk", "buy", "0.01", "0.0050", fee="0.0000001", cid="t-b"),
            fill("nx", "sell", "0.01", "0.0052", fee="0.00000011"),
        ]
        (d,) = compute(rows).doubles
        assert d.fees == D("0.00000021")
        assert d.result == D("0.000002") - D("0.00000021")

    def test_btcb2_fee_valued_at_fill_price(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.00002", asset="BTCB2")
        (d,) = compute(rows).doubles
        assert d.fees == D("0.00002") * D("0.0050")
        assert d.result == D("0.000002") - D("0.0000001")

    def test_fee_without_asset_and_zero_does_not_count(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0", asset=None)
        assert compute(rows).doubles[0].fees == 0

    def test_other_token_fee_unvalued_and_noted(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="3", asset="NEOX")
        (d,) = compute(rows).doubles
        assert d.fees == 0
        assert d.other_fees == (("NEOX", D("3")),)

    @pytest.mark.spec("RF-24")
    def test_summary_adds_fees_in_another_token(self):
        rows = plain_double("d-1") + plain_double("d-2", T0 + 10_000)
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.0025", asset="NKYC")
        rows[6] = fill("nx", "buy", "0.01", "0.0050", fee="0.0020", asset="NKYC", at=T0 + 11_000)
        assert compute(rows).summary.other_fees == (("NKYC", D("0.0045")),)

    def test_several_fills_per_leg_at_average_price(self):
        rows = [
            sent("t-b", "nx", "buy", "0.03", "0.0051", "d-1"),
            sent("t-s", "nk", "sell", "0.03", "0.0052", "d-1"),
            fill("nx", "buy", "0.01", "0.0049", at=T0 + 500),
            fill("nx", "buy", "0.02", "0.0050", at=T0 + 900),
            fill("nk", "sell", "0.03", "0.0052", cid="t-s"),
        ]
        (d,) = compute(rows).doubles
        assert d.buy_avg == (D("0.01") * D("0.0049") + D("0.02") * D("0.0050")) / D("0.03")
        assert d.result == D("0.03") * D("0.0052") - D("0.000049") - D("0.0001")

    def test_unequal_partials_match_the_minimum_and_the_rest_is_mismatch(self):
        rows = [
            sent("t-b", "nx", "buy", "0.05", "0.0050", "d-1"),
            sent("t-s", "nk", "sell", "0.05", "0.0052", "d-1"),
            fill("nx", "buy", "0.05", "0.0050"),
            fill("nk", "sell", "0.03", "0.0052", cid="t-s"),
        ]
        (d,) = compute(rows).doubles
        assert d.status == "closed"
        assert d.matched == D("0.03")
        assert d.mismatch == D("0.02")  # overbought
        assert d.result == D("0.03") * D("0.0002")

    def test_real_percent_of_what_was_paid(self):
        (d,) = compute(plain_double()).doubles
        assert d.result_pct == D("0.000002") / (D("0.01") * D("0.0050")) * 100


@pytest.mark.spec("RF-25", "CA-11")
class TestStates:
    def test_hanging_with_mismatch_does_not_add(self):
        rows = [
            sent("t-b", "nx", "buy", "0.05", "0.0050", "d-1"),
            sent("t-s", "nk", "sell", "0.05", "0.0052", "d-1"),
            fill("nx", "buy", "0.05", "0.0050"),
            fill("nk", "sell", "0.01", "0.0052", cid="t-s"),
            row("hanging_leg", T0 + 31_000, double="d-1", reason="…"),
        ]
        rep = compute(rows)
        assert rep.doubles[0].status == "hanging"
        assert rep.doubles[0].result is None
        assert rep.summary.hanging == 1
        assert rep.summary.closed == 0
        assert rep.summary.total == 0

    def test_hanging_later_equalized_counts_as_closed(self):
        rows = plain_double()
        rows.append(row("hanging_leg", T0 + 31_000, double="d-1", reason="…"))
        assert compute(rows).doubles[0].status == "closed"

    def test_leg_without_fills(self):
        rows = plain_double()[:3]  # the sell did not fill
        rep = compute(rows)
        assert rep.doubles[0].status == "no_fills"
        assert rep.doubles[0].result is None
        assert rep.summary.no_fills == 1

    def test_no_leg_filled(self):
        rows = plain_double()[:2]
        assert compute(rows).doubles[0].status == "no_fills"


@pytest.mark.spec("RF-25", "CA-11")
class TestOrigin:
    def test_bot_with_expected_net(self):
        rows = [fire("d-1", "3.5"), *plain_double()]
        (d,) = compute(rows).doubles
        assert d.origin == "bot"
        assert d.expected_pct == D("3.5")

    def test_manual_without_expected_net(self):
        (d,) = compute(plain_double()).doubles
        assert d.origin == "manual"
        assert d.expected_pct is None

    def test_simple_orders_are_not_doubles(self):
        rows = [
            row("order_sent", T0, "nk", "t-x", side="buy", qty="0.01", price="0.005"),
            fill("nk", "buy", "0.01", "0.005", cid="t-x"),
        ]
        assert compute(rows).doubles == ()

    def test_dry_run_is_ignored(self):
        rows = [sent("t-b", "nx", "buy", "0.01", "0.005", "d-1", mode="dry-run")]
        assert compute(rows).doubles == ()


@pytest.mark.spec("RF-25", "CA-11")
class TestAssignment:
    """Neoxa fills bring neither `order_id` nor `client_id` (D-45)."""

    def test_fill_by_order_id_through_order_ack(self):
        rows = plain_double()
        rows[3] = fill("nk", "sell", "0.01", "0.0052", oid="o-9")
        rows.insert(2, ack("t-d-1-s", "nk", "o-9"))
        assert compute(rows).doubles[0].status == "closed"

    def test_without_ids_goes_to_compatible_order(self):
        rows = [*plain_double("d-1", T0), *plain_double("d-2", T0 + 60_000, qty="0.02")]
        rep = compute(rows)
        assert [d.matched for d in rep.doubles] == [D("0.01"), D("0.02")]
        assert rep.summary.unassigned == 0

    def test_price_outside_limit_not_assigned(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0051")  # buy above the limit
        rep = compute(rows)
        assert rep.doubles[0].status == "no_fills"
        assert rep.summary.unassigned == 1

    def test_fill_before_sending_is_not_assigned(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", at=T0 - 5000)
        assert compute(rows).summary.unassigned == 1

    def test_stays_within_order_quantity(self):
        rows = plain_double()
        rows.insert(3, fill("nx", "buy", "0.01", "0.0050", at=T0 + 1500))  # one extra
        rep = compute(rows)
        assert rep.doubles[0].buy_filled == D("0.01")
        assert rep.summary.unassigned == 1

    def test_two_candidates_goes_to_the_one_sent_just_before(self):
        rows = [
            # a manual buy on Neoxa open for hours at the same limit
            row("order_sent", T0 - 3 * 3600_000, "nx", "t-m", side="buy", qty="1", price="0.0050"),
            *plain_double(),
        ]
        rep = compute(rows)
        assert rep.doubles[0].status == "closed"
        assert rep.summary.unassigned == 0

    def test_two_recent_candidates_stays_unassigned(self):
        rows = [
            row("order_sent", T0, "nx", "t-m", side="buy", qty="1", price="0.0050"),
            *plain_double(),
        ]
        rep = compute(rows)
        assert rep.doubles[0].status == "no_fills"
        assert rep.summary.unassigned == 1

    def test_rejected_order_is_no_candidate(self):
        rows = [
            row("order_sent", T0, "nx", "t-m", side="buy", qty="1", price="0.0050"),
            row("order_error", T0, "nx", "t-m", error="saldo"),
            *plain_double(),
        ]
        assert compute(rows).doubles[0].status == "closed"

    def test_repeated_fill_counts_once(self):
        rows = plain_double()
        rows.append(dict(rows[3]))
        assert compute(rows).doubles[0].sell_filled == D("0.01")


@pytest.mark.spec("RF-25", "CA-11")
class TestSummaryAndRange:
    def test_summary_and_curve(self):
        rows = [
            *plain_double("d-1", T0),  # +0.000002
            *plain_double("d-2", T0 + 60_000, buy="0.0053"),  # -0.000001
            *plain_double("d-3", T0 + 120_000, sell="0.0055"),  # +0.000005
        ]
        s = compute(rows).summary
        assert s.closed == 3
        assert s.total == D("0.000006")
        assert s.mean == D("0.000002")
        assert (s.best, s.worst) == (D("0.000005"), D("-0.000001"))
        curve = compute(rows).curve
        assert [v for _, v in curve] == [D("0.000002"), D("0.000001"), D("0.000006")]

    def test_no_doubles(self):
        s = compute([]).summary
        assert (s.closed, s.total, s.mean, s.best, s.worst) == (0, 0, None, None, None)

    def test_since_filters_by_double_start(self):
        rows = [*plain_double("d-1", T0 - DAY), *plain_double("d-2", T0)]
        rep = compute(rows, since_ms=T0 - 1000)
        assert [d.id for d in rep.doubles] == ["d-2"]

    def test_double_across_midnight_belongs_to_its_start_day(self):
        midnight = T0 - 12 * 3600_000
        rows = plain_double("d-1", midnight - 500)  # fills after midnight
        assert compute(rows, since_ms=midnight).doubles == ()
        assert compute(rows, since_ms=midnight - DAY).doubles[0].status == "closed"

    def test_ranges(self):
        start = T0 - 12 * 3600_000  # UTC midnight of T0's day
        assert range_start("dia", T0) == start
        assert range_start("7d", T0) == start - 6 * DAY
        assert range_start("todo", T0) is None


@pytest.mark.spec("RF-25")
class TestReading:
    def test_broken_lines_counted_and_harmless(self):
        good = [json.dumps(r) for r in plain_double()]
        rows, bad = read_rows([good[0], "{roto", "", good[1], '{"sin": "campos"}', *good[2:]])
        assert bad == 2
        assert compute(rows).doubles[0].status == "closed"

    def test_odd_row_values_break_nothing(self):
        rows = plain_double()
        rows.insert(2, row("fill", T0 + 1000, "nx", qty="x", price="0.005", side="buy"))
        rep = compute(rows)
        assert rep.doubles[0].status == "closed"
        assert rep.summary.bad_rows == 1


def fill_fee(venue, tid, fee, asset, oid=None, cid=None, at=T0 + 60_000):
    return row("fill_fee", at, venue, cid, trade_id=tid, order_id=oid, fee=fee, fee_asset=asset)


@pytest.mark.spec("RF-11", "RF-25")
class TestCorrectedFee:
    """d-2e18a2 (D-64): NonKYC redid in NKYC the fee of both buy fills."""

    def rows(self):
        return [
            sent("t-b", "nk", "buy", "0.0036", "0.00433551", "d-1"),
            sent("t-s", "nx", "sell", "0.0036", "0.00438221", "d-1"),
            ack("t-b", "nk", "6add927d"),
            fill("nk", "buy", "0.0018", "0.00433523", fee="0.000000015606828", oid="6add927d",
                 tid="c7"),
            fill("nk", "buy", "0.0018", "0.00432685", fee="0.00000001557666", oid="6add927d",
                 tid="c6"),
            fill("nx", "sell", "0.0036", "0.00441389", fee="0", asset=None, tid="amm"),
        ]  # fmt: skip

    def test_uncorrected_fee_in_btc(self):
        (d,) = compute(self.rows()).doubles
        assert d.fees == D("0.000000031183488") and d.other_fees == ()

    def test_correction_replaces_fee(self):
        rows = [
            *self.rows(),
            fill_fee("nk", "c7", "0.0000893166", "NKYC", oid="6add927d"),
            fill_fee("nk", "c6", "0.0000891439", "NKYC", oid="6add927d"),
        ]
        (d,) = compute(rows).doubles
        assert d.fees == 0
        assert d.other_fees == (("NKYC", D("0.0001784605")),)
        sold = D("0.0036") * D("0.00441389")
        paid = D("0.0018") * D("0.00433523") + D("0.0018") * D("0.00432685")
        assert d.result == sold - paid

    def test_two_corrections_the_last_counts(self):
        rows = [
            *self.rows(),
            fill_fee("nk", "c7", "0.00000002", "BTC"),
            fill_fee("nk", "c7", "0.0000893166", "NKYC"),
        ]
        (d,) = compute(rows).doubles
        assert d.fees == D("0.00000001557666")
        assert d.other_fees == (("NKYC", D("0.0000893166")),)

    def test_unknown_trade_is_ignored(self):
        rows = [*self.rows(), fill_fee("nk", "zz", "1", "NKYC")]
        (d,) = compute(rows).doubles
        assert d.other_fees == () and compute(rows).summary.bad_rows == 0


@pytest.mark.spec("RF-24", "RF-25")
class TestValuedTokenFee:
    """D-65: d-2e18a2's NKYC fee, valued at the NKYC/BTC mid, subtracts."""

    PRICE = D("0.00013089")
    NKYC = D("0.0001784605")
    SOLD = D("0.0036") * D("0.00441389")
    PAID = D("0.0018") * D("0.00433523") + D("0.0018") * D("0.00432685")

    def rows(self):
        return [
            *TestCorrectedFee().rows(),
            fill_fee("nk", "c7", "0.0000893166", "NKYC", oid="6add927d"),
            fill_fee("nk", "c6", "0.0000891439", "NKYC", oid="6add927d"),
        ]

    def test_priced_fee_subtracts(self):
        rep = compute(self.rows(), prices={"NKYC": self.PRICE})
        (d,) = rep.doubles
        assert d.token_fees == self.NKYC * self.PRICE
        assert d.result == self.SOLD - self.PAID - self.NKYC * self.PRICE
        assert d.other_fees == (("NKYC", self.NKYC),)  # the quantity is still shown
        assert rep.summary.total == d.result
        assert rep.summary.other_fees == ()  # nothing unvalued
        assert rep.summary.prices == (("NKYC", self.PRICE),)

    def test_paying_in_nkyc_beats_btc(self):
        """The 0.2 % fee in BTC (before the correction) against 0.15 % in NKYC."""
        (in_btc,) = compute(TestCorrectedFee().rows()).doubles
        (in_nkyc,) = compute(self.rows(), prices={"NKYC": self.PRICE}).doubles
        assert in_nkyc.result > in_btc.result

    def test_no_price_as_before(self):
        rep = compute(self.rows())
        (d,) = rep.doubles
        assert d.token_fees == 0 and d.result == self.SOLD - self.PAID
        assert rep.summary.other_fees == (("NKYC", self.NKYC),)
        assert rep.summary.prices == ()

    def test_other_asset_price_does_not_count(self):
        rep = compute(self.rows(), prices={"NEOX": D("1")})
        assert rep.doubles[0].token_fees == 0
        assert rep.summary.other_fees == (("NKYC", self.NKYC),)
        assert rep.summary.prices == ()
