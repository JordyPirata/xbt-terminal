"""Account value over time (T-69, RF-32, CA-17, D-69): the sample is built from the RF-05
inventory, written as a `value` event and read from the journal as a series with its
summary."""

import pytest

from xbt_terminal.core.inventory import inventory
from xbt_terminal.core.models import Balance, D
from xbt_terminal.core.results import compute
from xbt_terminal.core.valuation import (
    ValueSample,
    VenueValue,
    last_stretch,
    sample,
    sample_data,
    segments,
    series,
    summarize,
    thin,
)

from .test_results import T0, plain_double, row, ts

EVERY = 300_000


def bal(btc, btcb2):
    return {"BTC": Balance("BTC", D(btc), D(0)), "BTCB2": Balance("BTCB2", D(btcb2), D(0))}


def vrow(stamp, data, mode="live"):
    """Journal `value` row (`row` already uses `at` for the time)."""
    return {
        "ts": ts(stamp),
        "mode": mode,
        "venue": None,
        "event": "value",
        "client_id": None,
        "data": data,
    }


def value_row(at, total, nk="0", nx="0", every=EVERY, mode="live"):
    venues = {
        "nk": {"btc": nk, "btcb2": "0", "mid": "0.005", "value": nk},
        "nx": {"btc": nx, "btcb2": "0", "mid": "0.005", "value": nx},
    }
    return vrow(at, {"at": at, "every_ms": every, "value": total, "venues": venues}, mode)


def simple(at, total):
    return ValueSample(at, D(total), EVERY, ())


@pytest.mark.spec("RF-32", "CA-17")
class TestSample:
    def test_total_and_breakdown_per_venue(self):
        balances = {"nk": bal("0.1", "2"), "nx": bal("0.2", "4")}
        mids = {"nk": D("0.005"), "nx": D("0.006")}
        s = sample(inventory(balances, mids), mids, T0, EVERY)
        assert s is not None
        assert s.ts == T0 and s.every_ms == EVERY
        assert s.value == D("0.1") + D("2") * D("0.005") + D("0.2") + D("4") * D("0.006")
        assert s.venues == (
            VenueValue("nk", D("0.1"), D("2"), D("0.005"), D("0.110")),
            VenueValue("nx", D("0.2"), D("4"), D("0.006"), D("0.224")),
        )

    def test_no_mid_no_sample(self):
        balances = {"nk": bal("0.1", "2"), "nx": bal("0.2", "4")}
        mids = {"nk": D("0.005"), "nx": None}
        assert sample(inventory(balances, mids), mids, T0, EVERY) is None

    def test_no_balance_no_sample(self):
        mids = {"nk": D("0.005"), "nx": D("0.006")}
        inv = inventory({"nk": bal("0.1", "2"), "nx": None}, mids)
        assert sample(inv, mids, T0, EVERY) is None

    def test_round_trip_through_journal(self):
        balances = {"nk": bal("0.1", "2"), "nx": bal("0.2", "4")}
        mids = {"nk": D("0.005"), "nx": D("0.006")}
        s = sample(inventory(balances, mids), mids, T0, EVERY)
        assert s is not None
        plain = lambda v: v if isinstance(v, (int, dict)) else str(v)  # noqa: E731
        data = {k: plain(v) for k, v in sample_data(s).items()}
        data["venues"] = {
            venue_id: {k: str(v) for k, v in item.items()}
            for venue_id, item in data["venues"].items()
        }
        got, bad = series([vrow(T0 + 1234, data)], None)
        assert (got, bad) == ((s,), 0)


@pytest.mark.spec("RF-32", "CA-17")
class TestSeries:
    def test_only_those_in_range_in_order(self):
        rows = [
            value_row(T0 - 2 * EVERY, "1.0"),
            value_row(T0, "1.1"),
            *plain_double("d-1", T0),
            value_row(T0 + EVERY, "1.2", mode="dry-run"),  # the mode does not matter (D-69)
        ]
        got, bad = series(rows, T0)
        assert [(s.ts, s.value) for s in got] == [(T0, D("1.1")), (T0 + EVERY, D("1.2"))]
        assert bad == 0

    def test_broken_row_counted(self):
        broken = vrow(T0, {"at": T0, "value": "nan?", "venues": {}})
        got, bad = series([broken, value_row(T0 + EVERY, "1")], None)
        assert len(got) == 1 and bad == 1

    def test_travels_in_the_report(self):
        rows = [*plain_double("d-1", T0), value_row(T0, "1.5", nk="1", nx="0.5")]
        report = compute(rows, since_ms=T0)
        assert [s.value for s in report.values] == [D("1.5")]
        assert report.values[0].venues[0] == VenueValue("nk", D(1), D(0), D("0.005"), D(1))
        assert [d.id for d in report.doubles] == ["d-1"]

    def test_broken_row_adds_to_summary_bad_ones(self):
        report = compute([row("value", T0, value="x")])
        assert report.summary.bad_rows == 1


@pytest.mark.spec("RF-32", "CA-17")
class TestSummary:
    def test_first_last_change_max_min(self):
        got = summarize(
            tuple(simple(T0 + i * EVERY, v) for i, v in enumerate(("1.0", "1.3", "0.8", "1.1")))
        )
        assert got is not None
        assert (got.first, got.last) == (D("1.0"), D("1.1"))
        assert got.change == D("0.1")
        assert got.change_pct == D("10")
        assert (got.high, got.low, got.count) == (D("1.3"), D("0.8"), 4)
        assert (got.since, got.until) == (T0, T0 + 3 * EVERY)

    def test_no_samples(self):
        assert summarize(()) is None

    def test_first_at_zero_without_percent(self):
        got = summarize((simple(T0, "0"), simple(T0 + EVERY, "1")))
        assert got is not None and got.change_pct is None


@pytest.mark.spec("RF-32", "CA-17")
class TestGaps:
    def test_gap_over_two_intervals_cuts(self):
        s = [simple(T0 + i * EVERY, "1") for i in (0, 1, 2, 6, 7)]
        assert [len(seg) for seg in segments(s)] == [3, 2]

    def test_exactly_two_intervals_do_not_cut(self):
        s = [simple(T0, "1"), simple(T0 + 2 * EVERY, "1")]
        assert [len(seg) for seg in segments(s)] == [2]

    def test_no_samples_no_stretches(self):
        assert segments(()) == []

    def test_reduce_keeps_ends_and_size(self):
        s = tuple(simple(T0 + i * EVERY, str(i)) for i in range(1000))
        out = thin(s, 300)
        assert len(out) <= 300
        assert out[0] == s[0] and out[-1] == s[-1]
        assert thin(s[:10], 300) == s[:10]


def split(at, nk, nx):
    venues = (
        VenueValue("nk", D(nk), D(0), D("0.005"), D(nk)),
        VenueValue("nx", D(nx), D(0), D("0.005"), D(nx)),
    )
    return ValueSample(at, D(nk) + D(nx), EVERY, venues)


@pytest.mark.spec("RF-32", "CA-17")
class TestLastStretch:
    def test_from_the_last_total_jump(self):
        s = [
            simple(T0 + i * EVERY, v)
            for i, v in enumerate(("1.0", "1.001", "0.5", "0.501", "1.0", "1.002"))
        ]
        assert last_stretch(s) == tuple(s[4:])

    def test_no_jumps_means_everything(self):
        s = [simple(T0 + i * EVERY, v) for i, v in enumerate(("1.0", "1.04", "1.0"))]
        assert last_stretch(s) == tuple(s)

    def test_jump_per_venue_even_if_total_unchanged(self):
        # what left nk reaches nx in the same sample: the total does not move
        s = [
            split(T0, "0.6", "0.4"),
            split(T0 + EVERY, "0.3", "0.7"),
            split(T0 + 2 * EVERY, "0.3", "0.701"),
        ]
        assert last_stretch(s) == tuple(s[1:])

    def test_empty(self):
        assert last_stretch(()) == ()
