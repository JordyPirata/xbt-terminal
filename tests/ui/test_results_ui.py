"""Results window (T-40, RF-25): `r` key, `results` command, ranges, refresh with each
fill and closing."""

import json
import threading

import pytest

from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.results import ResultsService
from xbt_terminal.ui.screens import results as results_module
from xbt_terminal.ui.screens.results import ResultsScreen

from ..fakes import FakeFeed
from ..unit.test_results import DAY, T0, plain_double
from .local import local_app
from .test_orders_ui import Rig, type_command, until


def write(directory, day, rows):
    directory.mkdir(exist_ok=True)
    with (directory / f"journal-{day}.jsonl").open("a") as out:
        for r in rows:
            out.write(json.dumps(r) + "\n")


def make_app(tmp_path):
    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    hub = MarketHub(feeds, history=50, retry_s=0.01, clock=lambda: T0)
    results = ResultsService(tmp_path, clock=lambda: T0)
    return local_app(
        hub, book_levels=15, timeframe="1m", mode="read-only", clock=lambda: T0, results=results
    )


def screen(app) -> ResultsScreen:
    assert isinstance(app.screen, ResultsScreen)
    return app.screen


@pytest.mark.spec("RF-25", "CA-11")
class TestScreen:
    async def test_r_key_opens_with_day_and_esc_closes(self, tmp_path):
        write(tmp_path, "20261022", plain_double("d-1", T0))
        write(tmp_path, "20261020", plain_double("d-0", T0 - 2 * DAY))
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("r")
            await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
            s = screen(app)
            assert s.range == "dia"
            assert [d.id for d in s.report.doubles] == ["d-1"]
            assert "+200 sats" in s.summary_text()
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, ResultsScreen))

    async def test_keys_change_range(self, tmp_path):
        write(tmp_path, "20261022", plain_double("d-1", T0))
        write(tmp_path, "20261020", plain_double("d-0", T0 - 2 * DAY))
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("r")
            await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
            await pilot.press("2")
            await until(pilot, lambda: screen(app).range == "7d")
            assert len(screen(app).report.doubles) == 2
            await pilot.press("3")
            await until(pilot, lambda: screen(app).range == "todo")
            await pilot.press("1")
            await until(pilot, lambda: screen(app).range == "dia")
            assert len(screen(app).report.doubles) == 1

    async def test_command_with_range(self, tmp_path):
        write(tmp_path, "20261020", plain_double("d-0", T0 - 2 * DAY))
        rig = Rig(tmp_path / "rig", results=ResultsService(tmp_path, clock=lambda: T0))
        app = rig.app  # in dry-run: in read-only the command line is off
        async with app.run_test(size=(160, 45)) as pilot:
            await type_command(pilot, app, "results 7d")
            await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
            assert screen(app).range == "7d"
            assert len(screen(app).report.doubles) == 1

    async def test_table_with_hanging_and_no_doubles(self, tmp_path):
        rows = plain_double("d-1", T0)[:3]  # sell unfilled
        write(tmp_path, "20261022", rows)
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("r")
            await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
            assert screen(app).row_count() == 1
            assert "1 sin fills" in screen(app).summary_text()

    async def test_new_fill_recomputes(self, tmp_path):
        rows = plain_double("d-1", T0)
        write(tmp_path, "20261022", rows[:3])
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("r")
            await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
            assert screen(app).report.summary.closed == 0
            write(tmp_path, "20261022", rows[3:])
            app._on_account("nk", "fill")
            await until(pilot, lambda: screen(app).report.summary.closed == 1)

    async def test_no_service_warns(self, tmp_path):
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        hub = MarketHub(feeds, history=50, retry_s=0.01, clock=lambda: T0)
        app = local_app(hub, book_levels=15, timeframe="1m", mode="read-only", clock=lambda: T0)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("r")
            await pilot.pause(0.05)
            assert not isinstance(app.screen, ResultsScreen)
            assert "resultados" in app.last_message


@pytest.mark.spec("RF-24", "RF-25")
def test_summary_warns_of_token_fees(tmp_path):
    from ..unit.test_results import fill

    rows = plain_double("d-1", T0)
    rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.0025", asset="NKYC")
    write(tmp_path, "20261022", rows)
    s = ResultsScreen(ResultsService(tmp_path, clock=lambda: T0), "dia", print)
    s.report = s._source.load("dia")
    assert "+ 0.0025 NKYC en fees, sin descontar" in s.summary_text()


@pytest.mark.spec("RF-24", "RF-25")
async def test_on_open_asks_price_and_subtracts_fee(tmp_path):
    """D-65: 0.0025 NKYC at 0.0001 BTC = 25 sats less than the 200 without the fee."""
    from xbt_terminal.core.models import D

    from ..unit.test_results import fill

    rows = plain_double("d-1", T0)
    rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.0025", asset="NKYC")
    write(tmp_path, "20261022", rows)

    async def price():
        return D("0.0001")

    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    hub = MarketHub(feeds, history=50, retry_s=0.01, clock=lambda: T0)
    results = ResultsService(tmp_path, clock=lambda: T0, pricers={"NKYC": price})
    app = local_app(
        hub, book_levels=15, timeframe="1m", mode="read-only", clock=lambda: T0, results=results
    )
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("r")
        await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
        s = screen(app)
        await until(pilot, lambda: s.report.summary.prices)
        assert "+175 sats" in s.summary_text()
        assert "NKYC a 0.00010000 BTC" in s.summary_text()
        assert "sin descontar" not in s.summary_text()
        (d,) = s.report.doubles
        assert ResultsScreen._cells(d, {"NKYC"})[7] == "25 (0.0025 NKYC)"


@pytest.mark.spec("RF-25", "RF-32")
async def test_periodic_reload_reads_off_the_ui_thread(tmp_path, monkeypatch):
    """Every `RESULTS_EVERY_S` and with each fill, the journal is read in a worker thread
    (T-94); opening the window still draws at once."""
    monkeypatch.setattr(results_module, "RESULTS_EVERY_S", 0.05)
    write(tmp_path, "20261022", plain_double("d-1", T0))
    app = make_app(tmp_path)
    service = app.results
    threads: list[str] = []
    original = service.load

    def load(rng):
        threads.append(threading.current_thread().name)
        return original(rng)

    monkeypatch.setattr(service, "load", load)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("r")
        await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
        assert threads[0] == threading.main_thread().name
        await until(pilot, lambda: len(threads) >= 3)
        assert all(name != threading.main_thread().name for name in threads[1:])
        write(tmp_path, "20261022", plain_double("d-2", T0 + 60_000))
        await until(pilot, lambda: len(screen(app).report.doubles) == 2)
