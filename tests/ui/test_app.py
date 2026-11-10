"""Read-only Textual app (T-13) with fake venues: layout, books, candles, size warning
and book drawing latency."""

import asyncio
import itertools
import time

import pytest

from xbt_terminal.core.models import BookDelta, D, Level
from xbt_terminal.services.market import MarketHub
from xbt_terminal.ui.app import TooSmall
from xbt_terminal.ui.panels import AMM_TAG, BookPanel, ChartPanel, CommandLine, CrossPanel

from ..fakes import T0, FakeFeed, book
from .local import local_app

NOW = T0 + 5 * 3_600_000
SIZE = (160, 45)


def make_app(levels=15, **feeds):
    feeds = feeds or {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    hub = MarketHub(feeds, history=50, retry_s=0.01, clock=lambda: NOW)
    return local_app(hub, book_levels=levels, timeframe="1m", mode="read-only"), feeds


async def until(pilot, predicate, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await pilot.pause(0.01)


def panels(app):
    return {
        "chart": list(app.query(ChartPanel)),
        "book": list(app.query(BookPanel)),
        "info": list(app.query(CrossPanel)),
        "cmd": list(app.query(CommandLine)),
    }


@pytest.mark.spec("RNF-02", "RF-02", "RF-03")
class TestLayout:
    async def test_mounts_6_panels_at_160x45(self):
        app, _ = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            panel_groups = panels(app)
            assert [len(panel_list) for panel_list in panel_groups.values()] == [2, 2, 1, 1]
            widgets = [widget for group in panel_groups.values() for widget in group]
            assert all(widget.display and widget.region.area > 0 for widget in widgets)
            assert not app.query_one(TooSmall).display

    async def test_nonkyc_left_neoxa_right(self):
        app, _ = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            for kind in (ChartPanel, BookPanel):
                left, right = sorted(app.query(kind), key=lambda widget: widget.region.x)
                assert (left.venue, right.venue) == ("nk", "nx")
                assert left.region.y == right.region.y

    async def test_charts_top_books_middle_bar_bottom(self):
        app, _ = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            chart = app.query_one("#chart-nk").region
            book_ = app.query_one("#book-nk").region
            info = app.query_one(CrossPanel).region
            assert chart.y < book_.y < info.y
            assert info.width >= 158  # takes both columns

    @pytest.mark.parametrize("size", [(159, 45), (160, 44), (100, 30)])
    async def test_below_minimum_notice(self, size):
        app, _ = make_app()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            warning = app.query_one(TooSmall)
            assert warning.display and not app.query_one("#layout").display
            assert f"{size[0]}×{size[1]}" in warning.text and "160×45" in warning.text

    async def test_enlarging_brings_layout_back(self):
        app, _ = make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.resize_terminal(*SIZE)
            await pilot.pause()
            assert app.query_one("#layout").display and not app.query_one(TooSmall).display


@pytest.mark.spec("RF-02")
class TestBook:
    async def test_levels_cumulative_and_spread(self):
        app, feeds = make_app()
        async with app.run_test(size=SIZE) as pilot:
            feeds["nk"].push(book("nk", "0.00720000", "0.00730000", seq=1))
            panel = app.query_one("#book-nk", BookPanel)
            await until(pilot, lambda: "0.00720000" in panel.text)
            text = panel.text
            assert "0.00730000" in text and "0.00730100" in text
            assert "0.00719900" in text
            assert "3.0000" in text  # bid cumulative: 1 + 2 (4 decimals on nk)
            assert "spread 0.00010000" in text and "10000 sats" in text

    async def test_amm_levels_marked(self):
        app, feeds = make_app()
        async with app.run_test(size=SIZE) as pilot:
            feeds["nx"].push(book("nx", "0.00720000", "0.00730000"))
            panel = app.query_one("#book-nx", BookPanel)
            await until(pilot, lambda: "0.00719900" in panel.text)
            amm_line = next(ln for ln in panel.text.splitlines() if "0.00719900" in ln)
            assert "0.00719900" + AMM_TAG in amm_line
            normal = next(ln for ln in panel.text.splitlines() if "0.00720000" in ln)
            assert "0.00720000" + AMM_TAG not in normal
            assert "liquidez AMM" in panel.border_subtitle

    async def test_respects_level_count(self):
        app, feeds = make_app(levels=1)
        async with app.run_test(size=SIZE) as pilot:
            feeds["nk"].push(book("nk", "0.00720000", "0.00730000"))
            panel = app.query_one("#book-nk", BookPanel)
            await until(pilot, lambda: "0.00720000" in panel.text)
            assert "0.00719900" not in panel.text and "0.00730100" not in panel.text

    async def test_invalid_book_says_so(self):
        app, feeds = make_app()
        async with app.run_test(size=SIZE) as pilot:
            feeds["nk"].push(book("nk", "0.00720000", "0.00730000", seq=1))
            panel = app.query_one("#book-nk", BookPanel)
            await until(pilot, lambda: "0.00720000" in panel.text)
            feeds["nk"].push(BookDelta("nk", (), (), T0, 5))  # sequence gap
            await until(pilot, lambda: "esperando snapshot" in panel.text)
            assert "0.00720000" not in panel.text  # no old prices

    async def test_no_data_yet(self):
        app, _ = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert "esperando" in app.query_one("#book-nx", BookPanel).text


@pytest.mark.spec("RNF-06")
async def test_delta_drawn_within_500_ms():
    app, feeds = make_app()
    async with app.run_test(size=SIZE) as pilot:
        feeds["nk"].push(book("nk", "0.00720000", "0.00730000", seq=1))
        panel = app.query_one("#book-nk", BookPanel)
        await until(pilot, lambda: "0.00720000" in panel.text)
        start = time.perf_counter()
        feeds["nk"].push(BookDelta("nk", (Level(D("0.00725555"), D("1")),), (), T0, 2))
        await until(pilot, lambda: "0.00725555" in panel.text, timeout=0.5)
        assert time.perf_counter() - start < 0.5


@pytest.mark.spec("RF-03")
class TestCandleBars:
    async def test_each_venue_its_chart_with_history(self):
        app, _ = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await until(
                pilot,
                lambda: all(candle_chart.candles for candle_chart in app.query(ChartPanel)),
                timeout=3,
            )
            for chart in app.query(ChartPanel):
                assert chart.timeframe == "1m"
                assert all(candle_chart.ts_open % 60_000 == 0 for candle_chart in chart.candles)

    @pytest.mark.parametrize(
        "key,tf,step",
        [
            ("2", "5m", 300_000),
            ("3", "1h", 3_600_000),
            ("4", "4h", 14_400_000),
            ("5", "1d", 86_400_000),
        ],
    )
    async def test_timeframe_shortcuts(self, key, tf, step):
        app, _ = make_app()
        async with app.run_test(size=SIZE) as pilot:
            await until(
                pilot,
                lambda: all(candle_chart.candles for candle_chart in app.query(ChartPanel)),
                timeout=3,
            )
            await pilot.press(key)
            await until(
                pilot,
                lambda: all(candle_chart.timeframe == tf for candle_chart in app.query(ChartPanel)),
                timeout=3,
            )
            for chart in app.query(ChartPanel):
                assert chart.candles and all(
                    candle_chart.ts_open % step == 0 for candle_chart in chart.candles
                )
            await pilot.press("1")
            await until(
                pilot,
                lambda: all(
                    candle_chart.timeframe == "1m" for candle_chart in app.query(ChartPanel)
                ),
            )


async def test_q_exits():
    app, _ = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("q")
        await pilot.pause()
        assert app.return_code == 0 or not app.is_running


@pytest.mark.spec("RF-02", "RNF-02")
async def test_book_rows_fit_unsplit():
    app, feeds = make_app()
    async with app.run_test(size=SIZE) as pilot:
        feeds["nx"].push(book("nx", "0.00720000", "0.00730000"))
        panel = app.query_one("#book-nx", BookPanel)
        await until(pilot, lambda: "0.00720000" in panel.text)
        width = panel.content_region.width
        assert all(len(line) <= width for line in panel.text.splitlines())


@pytest.mark.spec("RF-03")
@pytest.mark.parametrize(
    "tf,fmt",
    [("1m", "%H:%M"), ("5m", "%H:%M"), ("1h", "%d %H:%M"), ("4h", "%d %H:%M"), ("1d", "%m-%d")],
)
def test_axis_format_per_timeframe(tf, fmt):
    """In 4h the hour alone repeats daily, and in 1d it says nothing: the date is added."""
    from xbt_terminal.ui.panels import axis_format

    assert axis_format(tf) == fmt


@pytest.mark.spec("RF-03")
@pytest.mark.parametrize(
    "count,canvas,expected",
    [
        (70, 85, (70, 1)),  # more columns than candles: one per candle, no stray gaps
        (300, 85, (85, 1)),  # more candles than columns: only those that fit
        (40, 85, (40, 2)),  # few candles: two columns each
        (20, 85, (20, 4)),
        (1, 85, (1, 4)),  # the step has a cap: a lone candle does not take everything
        (10, 0, (0, 1)),
    ],
)
def test_column_split(count, canvas, expected):
    """D-24: each candle gets a whole number of columns, the same for all."""
    from xbt_terminal.ui.panels import candle_layout

    assert candle_layout(count, canvas) == expected


@pytest.mark.spec("RF-03")
def test_y_axis_fits_bodies_not_wicks():
    """D-67: a stray wick does not crush the candles; the Y axis follows the bodies and the
    wick sticking out is cut at the border."""
    from xbt_terminal.core.models import Candle

    candles = (
        Candle(0, D("10"), D("50"), D("9"), D("11"), D("1")),
        Candle(60_000, D("11"), D("12"), D("1"), D("12"), D("1")),
    )
    assert ChartPanel._range(candles) == (10.0, 12.0)
    chart = ChartPanel("nk")
    chart.candles = candles
    chart._draw(80)  # the out of range wick does not break drawing


def _candle_columns(chart: ChartPanel) -> tuple[list[int], int]:
    lines = chart.render().plain.splitlines()
    top = next(line for line in lines if "┌" in line)
    left, right = top.index("┌"), top.index("┐")
    rows = lines[
        lines.index(top) + 1 : next(i for i, text_line in enumerate(lines) if "└" in text_line)
    ]
    return sorted(
        {column for row in rows for column in range(left + 1, right) if row[column] in "█│"}
    ), right


@pytest.mark.spec("RF-03")
@pytest.mark.parametrize("key", ["1", "5"])
async def test_candles_without_stray_gaps(key):
    """D-24: candles go at a fixed step with the last against the right border; plotext used
    to spread N candles over M columns leaving random empty columns."""
    app, _ = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(key)
        await until(
            pilot,
            lambda: all(candle_chart.candles for candle_chart in app.query(ChartPanel)),
            timeout=3,
        )
        await pilot.pause()
        for chart in app.query(ChartPanel):
            cols, right = _candle_columns(chart)
            steps = {current - previous for previous, current in itertools.pairwise(cols)}
            assert len(steps) == 1, steps
            assert cols[-1] == right - 1
