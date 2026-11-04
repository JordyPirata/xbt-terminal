import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import D, Order
from xbt_terminal.core.secrets import Redactor, Secret

CANARY = "nk_sec_CANARIO_77e1"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 19, 21, 4, 11, 532_000, tzinfo=UTC))


@pytest.fixture
def journal(tmp_path, clock):
    j = Journal(tmp_path, mode="dry-run", redactor=Redactor([Secret(CANARY)]), clock=clock)
    yield j
    j.close()


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def only_file(tmp_path: Path) -> Path:
    (path,) = tmp_path.glob("journal-*.jsonl")
    return path


@pytest.mark.spec("RF-11")
class TestFormat:
    def test_one_json_line_per_event(self, journal, tmp_path):
        journal.write("order_sent", venue="nk", client_id="t-1", data={"side": "buy"})
        journal.write("order_ack", venue="nk", client_id="t-1", data={"order_id": "abc"})
        journal.write("conn", venue="nx", data={"status": "up"})
        rows = lines(only_file(tmp_path))
        assert [r["event"] for r in rows] == ["order_sent", "order_ack", "conn"]
        assert set(rows[0]) == {"ts", "mode", "venue", "event", "client_id", "data"}
        assert rows[0]["mode"] == "dry-run"
        assert rows[2]["client_id"] is None

    def test_iso_utc_timestamp_with_milliseconds(self, journal, tmp_path):
        journal.write("error", data={})
        assert lines(only_file(tmp_path))[0]["ts"] == "2026-10-19T21:04:11.532Z"

    def test_decimal_as_exact_string(self, journal, tmp_path):
        journal.write("order_sent", venue="nk", data={"price": D("850.00"), "qty": D("0.0100")})
        data = lines(only_file(tmp_path))[0]["data"]
        assert data == {"price": "850.00", "qty": "0.0100"}

    def test_decimal_without_scientific_notation(self, journal, tmp_path):
        """A 6 sat fee is D("6E-8") when read from the venue: goes as 0.00000006."""
        journal.write("fill", venue="nx", data={"fee": D("6E-8"), "qty": D("1.5E+3")})
        assert lines(only_file(tmp_path))[0]["data"] == {"fee": "0.00000006", "qty": "1500"}

    def test_models_serialize(self, journal, tmp_path):
        order = Order("nk", "abc", "t-1", "buy", D("850.00"), D("0.01"), D("0"), "open", 1)
        journal.write("order_ack", venue="nk", client_id="t-1", data={"order": order})
        assert lines(only_file(tmp_path))[0]["data"]["order"]["price"] == "850.00"

    def test_unknown_event(self, journal):
        with pytest.raises(ValueError):
            journal.write("order_snet", data={})

    def test_unserializable_type(self, journal, tmp_path):
        with pytest.raises(TypeError):
            journal.write("error", data={"x": object()})
        # the failure leaves no half line
        assert not list(tmp_path.glob("journal-*.jsonl")) or lines(only_file(tmp_path)) == []


@pytest.mark.spec("RF-11")
class TestAppendOnly:
    def test_each_line_is_on_disk_when_written(self, journal, tmp_path):
        journal.write("fill", venue="nx", data={"qty": D("0.01")})
        assert len(lines(only_file(tmp_path))) == 1  # no close()

    def test_reopening_does_not_truncate(self, tmp_path, clock):
        for _ in range(2):
            j = Journal(tmp_path, mode="live", redactor=Redactor([]), clock=clock)
            j.write("cancel_sent", venue="nk", data={})
            j.close()
        assert len(lines(only_file(tmp_path))) == 2

    def test_daily_rotation_in_utc(self, journal, tmp_path, clock):
        clock.now = datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)
        journal.write("conn", data={})
        clock.now += timedelta(seconds=2)
        journal.write("conn", data={})
        names = sorted(p.name for p in tmp_path.glob("journal-*.jsonl"))
        assert names == ["journal-20261019.jsonl", "journal-20261020.jsonl"]

    def test_creates_the_directory(self, tmp_path, clock):
        j = Journal(tmp_path / "logs" / "sub", mode="live", redactor=Redactor([]), clock=clock)
        j.write("conn", data={})
        j.close()
        assert only_file(tmp_path / "logs" / "sub")


@pytest.mark.spec("RF-12", "CA-07")
class TestNoSecrets:
    def test_secret_anywhere_is_masked(self, journal, tmp_path):
        journal.write(
            "order_error",
            venue="nk",
            client_id=f"t-{CANARY}",
            data={
                "error": f"HTTP 401: invalid key {CANARY}",
                CANARY: [{"nested": CANARY}],
                "obj": Secret(CANARY),
            },
        )
        text = only_file(tmp_path).read_text(encoding="utf-8")
        assert CANARY not in text
        assert text.count("***") == 5

    def test_secret_with_json_escaped_characters(self, tmp_path, clock):
        tricky = 'ab"c\\d/é'
        j = Journal(tmp_path, mode="live", redactor=Redactor([Secret(tricky)]), clock=clock)
        j.write("error", data={"msg": f"x{tricky}y"})
        j.close()
        row = lines(only_file(tmp_path))[0]
        assert row["data"]["msg"] == "x***y"


@pytest.mark.spec("RNF-18")
def test_journal_user_readable_only(tmp_path, clock):
    directory = tmp_path / "datos"
    with Journal(directory, mode="live", redactor=Redactor([]), clock=clock) as j:
        j.write("conn", venue="nk", data={})
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(only_file(directory).stat().st_mode) == 0o600


@pytest.mark.spec("RNF-18")
def test_existing_journal_tightened_to_0600(tmp_path, clock):
    old = tmp_path / "journal-20261019.jsonl"
    old.write_text("")
    old.chmod(0o664)
    with Journal(tmp_path, mode="live", redactor=Redactor([]), clock=clock) as j:
        j.write("conn", venue="nk", data={})
    assert stat.S_IMODE(old.stat().st_mode) == 0o600
