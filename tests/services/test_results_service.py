"""Reading the journals for the results window (T-40, RF-25, D-44)."""

import json
import os

import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.core.models import D
from xbt_terminal.services.results import ResultsService

from ..unit.test_results import DAY, T0, fill, plain_double


def write(directory, day, rows, extra=()):
    directory.mkdir(exist_ok=True)
    lines = [json.dumps(row) for row in rows] + list(extra)
    (directory / f"journal-{day}.jsonl").write_text("\n".join(lines) + "\n")


@pytest.mark.spec("RF-25", "CA-11")
class TestService:
    def test_ranges_by_utc_day(self, tmp_path):
        # T0 = 2026-10-22 12:00 UTC
        write(tmp_path, "20261012", plain_double("d-viejo", T0 - 10 * DAY))
        write(tmp_path, "20261020", plain_double("d-semana", T0 - 2 * DAY))
        write(tmp_path, "20261022", plain_double("d-hoy", T0))
        svc = ResultsService(tmp_path, clock=lambda: T0)
        assert [double.id for double in svc.load("dia").doubles] == ["d-hoy"]
        assert [double.id for double in svc.load("7d").doubles] == ["d-semana", "d-hoy"]
        assert [double.id for double in svc.load("todo").doubles] == [
            "d-viejo",
            "d-semana",
            "d-hoy",
        ]

    def test_yesterdays_double_with_fills_today(self, tmp_path):
        midnight = T0 - 12 * 3600_000
        rows = plain_double("d-1", midnight - 500)
        write(tmp_path, "20261021", rows[:2])  # sends before midnight
        write(tmp_path, "20261022", rows[2:])  # fills after
        svc = ResultsService(tmp_path, clock=lambda: T0)
        assert svc.load("dia").doubles == ()
        assert svc.load("7d").doubles[0].result == D("0.000002")

    def test_no_directory_or_journals(self, tmp_path):
        rep = ResultsService(tmp_path / "nada", clock=lambda: T0).load("todo")
        assert rep.doubles == ()
        assert rep.summary.closed == 0

    def test_broken_lines_are_counted(self, tmp_path):
        write(tmp_path, "20261022", plain_double(), extra=["{roto"])
        rep = ResultsService(tmp_path, clock=lambda: T0).load("dia")
        assert rep.summary.bad_rows == 1
        assert rep.summary.closed == 1

    def test_other_files_ignored(self, tmp_path):
        write(tmp_path, "20261022", plain_double())
        (tmp_path / "diag.log").write_text("no es json\n")
        (tmp_path / "journal-raro.jsonl").write_text("{roto\n")
        rep = ResultsService(tmp_path, clock=lambda: T0).load("todo")
        assert rep.summary.bad_rows == 0


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Pricer:
    """Fake `token_price`: counts requests; `fail` raises VenueError."""

    def __init__(self, price="0.00013089"):
        self.price, self.calls, self.fail = D(price), 0, False

    async def __call__(self):
        self.calls += 1
        if self.fail:
            raise VenueError("sin red")
        return self.price


def nkyc_double(tmp_path):
    rows = plain_double("d-1", T0)
    rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.0025", asset="NKYC")
    write(tmp_path, "20261022", rows)


@pytest.mark.spec("RF-24", "RF-25")
class TestTokenPrice:
    async def test_price_not_asked_stays_unvalued(self, tmp_path):
        nkyc_double(tmp_path)
        svc = ResultsService(tmp_path, clock=lambda: T0, pricers={"NKYC": Pricer()})
        assert svc.load("dia").summary.other_fees == (("NKYC", D("0.0025")),)

    async def test_with_price_it_subtracts(self, tmp_path):
        nkyc_double(tmp_path)
        svc = ResultsService(tmp_path, clock=lambda: T0, pricers={"NKYC": Pricer()})
        await svc.refresh_prices()
        rep = svc.load("dia")
        assert rep.summary.prices == (("NKYC", D("0.00013089")),)
        assert rep.doubles[0].token_fees == D("0.0025") * D("0.00013089")

    async def test_price_kept_one_minute(self, tmp_path):
        clock, pricer = Clock(), Pricer()
        svc = ResultsService(tmp_path, clock=clock, pricers={"NKYC": pricer})
        await svc.refresh_prices()
        clock.now += 59_000
        await svc.refresh_prices()
        assert pricer.calls == 1
        clock.now += 1_000
        await svc.refresh_prices()
        assert pricer.calls == 2

    async def test_failure_stays_unvalued_and_says_so(self, tmp_path):
        nkyc_double(tmp_path)
        pricer = Pricer()
        pricer.fail = True
        svc = ResultsService(tmp_path, clock=lambda: T0, pricers={"NKYC": pricer})
        await svc.refresh_prices()
        assert svc.load("dia").summary.other_fees == (("NKYC", D("0.0025")),)
        assert svc.price_errors == {"NKYC": "sin red"}

    async def test_failure_keeps_previous_price(self, tmp_path):
        clock, pricer = Clock(), Pricer()
        svc = ResultsService(tmp_path, clock=clock, pricers={"NKYC": pricer})
        await svc.refresh_prices()
        pricer.fail = True
        clock.now += 60_000
        await svc.refresh_prices()
        assert svc.prices == {"NKYC": D("0.00013089")}


def fresh(directory, rng):
    """The report a service with nothing cached gives: what every load gave before T-94."""
    return ResultsService(directory, clock=lambda: T0).load(rng)


def append(directory, day, rows, extra=()):
    lines = [json.dumps(row) for row in rows] + list(extra)
    with (directory / f"journal-{day}.jsonl").open("a") as out:
        out.write("".join(line + "\n" for line in lines))


class Reads:
    """Counts what the service reads of each journal: (file name, from byte)."""

    def __init__(self, svc, monkeypatch):
        self.calls: list[tuple[str, int]] = []
        original = svc._read_from

        def counting(path, offset):
            self.calls.append((path.name, offset))
            return original(path, offset)

        monkeypatch.setattr(svc, "_read_from", counting)


@pytest.mark.spec("RF-25", "RF-32")
class TestCache:
    """Closed days are read once; today's file, only what was appended (T-94)."""

    @pytest.fixture
    def journals(self, tmp_path):
        write(tmp_path, "20261012", plain_double("d-viejo", T0 - 10 * DAY))
        write(tmp_path, "20261020", plain_double("d-semana", T0 - 2 * DAY))
        write(tmp_path, "20261022", plain_double("d-hoy", T0), extra=["{roto"])
        return tmp_path

    @pytest.mark.parametrize("rng", ["dia", "7d", "todo"])
    def test_same_report_as_without_cache(self, journals, rng):
        svc = ResultsService(journals, clock=lambda: T0)
        for other in ("todo", "dia", "7d"):
            svc.load(other)
        assert svc.load(rng) == fresh(journals, rng)
        append(journals, "20261022", plain_double("d-tarde", T0 + 3600_000))
        assert svc.load(rng) == fresh(journals, rng)

    def test_appended_line_appears_in_next_load(self, journals):
        svc = ResultsService(journals, clock=lambda: T0)
        assert [double.id for double in svc.load("dia").doubles] == ["d-hoy"]
        append(journals, "20261022", plain_double("d-tarde", T0 + 3600_000))
        assert [double.id for double in svc.load("dia").doubles] == ["d-hoy", "d-tarde"]

    def test_unchanged_files_are_not_read_again(self, journals, monkeypatch):
        svc = ResultsService(journals, clock=lambda: T0)
        reads = Reads(svc, monkeypatch)
        svc.load("todo")
        assert len(reads.calls) == 3
        reads.calls.clear()
        svc.load("todo")
        svc.load("7d")
        assert reads.calls == []

    def test_only_the_appended_bytes_are_read(self, journals, monkeypatch):
        svc = ResultsService(journals, clock=lambda: T0)
        svc.load("todo")
        size = (journals / "journal-20261022.jsonl").stat().st_size
        reads = Reads(svc, monkeypatch)
        append(journals, "20261022", plain_double("d-tarde", T0 + 3600_000))
        svc.load("todo")
        assert reads.calls == [("journal-20261022.jsonl", size)]

    def test_rewritten_file_is_read_whole(self, journals, monkeypatch):
        svc = ResultsService(journals, clock=lambda: T0)
        svc.load("todo")
        reads = Reads(svc, monkeypatch)
        write(journals, "20261020", plain_double("d-otro", T0 - 2 * DAY, qty="0.02"))
        assert svc.load("todo") == fresh(journals, "todo")
        assert reads.calls == [("journal-20261020.jsonl", 0)]

    def test_same_size_but_newer_is_read_whole(self, journals, monkeypatch):
        path = journals / "journal-20261020.jsonl"
        svc = ResultsService(journals, clock=lambda: T0)
        svc.load("todo")
        text = path.read_text()
        path.write_text(text.replace("d-semana", "d-semanb"))
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        reads = Reads(svc, monkeypatch)
        assert "d-semanb" in [double.id for double in svc.load("todo").doubles]
        assert reads.calls == [("journal-20261020.jsonl", 0)]

    def test_half_written_line_counts_until_completed(self, journals):
        svc = ResultsService(journals, clock=lambda: T0)
        svc.load("dia")
        row = json.dumps(plain_double("d-tarde", T0 + 3600_000)[0])
        path = journals / "journal-20261022.jsonl"
        with path.open("a") as out:
            out.write(row[:10])
        assert svc.load("dia") == fresh(journals, "dia")
        assert svc.load("dia").summary.bad_rows == 2
        with path.open("a") as out:
            out.write(row[10:] + "\n")
        assert svc.load("dia") == fresh(journals, "dia")
        assert svc.load("dia").summary.bad_rows == 1

    def test_deleted_file_disappears(self, journals):
        svc = ResultsService(journals, clock=lambda: T0)
        svc.load("todo")
        (journals / "journal-20261012.jsonl").unlink()
        assert [double.id for double in svc.load("todo").doubles] == ["d-semana", "d-hoy"]
