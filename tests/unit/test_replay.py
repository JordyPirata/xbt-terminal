"""Matching against the recorded book and report summary (T-87, RF-34, RF-35): the pure
part of the test bench."""

from decimal import Decimal

import pytest

from xbt_terminal.core.models import D, Level
from xbt_terminal.core.replay import ReplayReport, match, render, summarize

ASKS = (Level(D("0.010"), D("1")), Level(D("0.011"), D("2")), Level(D("0.012"), D("3")))
BIDS = (Level(D("0.009"), D("1")), Level(D("0.008"), D("2")))


def nobody(level):
    return False


@pytest.mark.spec("RF-34", "CA-19")
class TestMatch:
    def test_buy_by_levels_up_to_its_limit(self):
        assert match("buy", D("0.011"), D("2"), ASKS, nobody) == [
            (D("0.010"), D("1")),
            (D("0.011"), D("1")),
        ]

    def test_sell_by_levels_down_to_its_limit(self):
        assert match("sell", D("0.008"), D("2"), BIDS, nobody) == [
            (D("0.009"), D("1")),
            (D("0.008"), D("1")),
        ]

    def test_short_of_limit_does_not_match(self):
        assert match("buy", D("0.0099"), D("1"), ASKS, nobody) == []
        assert match("sell", D("0.0091"), D("1"), BIDS, nobody) == []

    def test_no_depth_matches_what_there_is(self):
        assert match("buy", D("0.010"), D("5"), ASKS, nobody) == [(D("0.010"), D("1"))]

    def test_competitor_takes_whole_levels(self):
        first = ASKS[0]
        taken = match("buy", D("0.011"), D("2"), ASKS, lambda lv: lv == first)
        assert taken == [(D("0.011"), D("2"))]

    def test_only_asks_for_reachable_levels(self):
        asked = []
        match("buy", D("0.010"), D("1"), ASKS, lambda lv: asked.append(lv) or False)
        assert asked == [ASKS[0]]


def row(event, **data):
    return {"ts": "2026-11-04T00:00:00.000Z", "mode": "dry-run", "event": event, "data": data}


@pytest.mark.spec("RF-35", "CA-20")
class TestSummarize:
    def test_per_100_shots(self):
        rows = [
            *[row("bot_fire", double=f"d-{i}") for i in range(4)],
            row("bot_miss", double="d-1"),
            row("bot_state", status="halted", reason="hanging_leg: compra 1 y venta 0"),
            row("bot_state", status="armed", reason=None),
        ]
        report = summarize(rows, crossings=8, filled=2, net=D("0.0001"), duration_ms=90_000)
        assert report == ReplayReport(
            minutes=D("1.5"), crossings=8, fires=4, filled=2, capture_pct=D(25),
            net=D("0.0001"), misses=1, miss_per_100=D(25), hanging=1,
            hanging_per_100=D(25), halted=None,
        )  # fmt: skip

    def test_no_crossings_or_shots_does_not_divide(self):
        report = summarize([], crossings=0, filled=0, net=Decimal(0), duration_ms=60_000)
        assert report.capture_pct is None and report.miss_per_100 is None

    def test_another_stop_is_reported(self):
        rows = [row("bot_state", status="halted", reason="rejected: saldo")]
        report = summarize(rows, crossings=0, filled=0, net=Decimal(0), duration_ms=1)
        assert report.halted == "rejected: saldo"

    def test_hanging_leg_is_not_the_final_stop(self):
        rows = [row("bot_state", status="halted", reason="hanging_leg: x")]
        report = summarize(rows, crossings=0, filled=0, net=Decimal(0), duration_ms=1)
        assert report.halted is None

    def test_text_has_each_measure(self):
        rows = [row("bot_fire", double="d-1")]
        report = summarize(rows, crossings=2, filled=1, net=D("0.00001234"), duration_ms=60_000)
        text = render(report)
        for piece in ("1 min", "cruces sobre el umbral: 2", "disparos: 1", "dobles llenas: 1",
                      "tasa de captura: 50.0 %", "0.00001234 BTC", "1234 sats"):  # fmt: skip
            assert piece in text
