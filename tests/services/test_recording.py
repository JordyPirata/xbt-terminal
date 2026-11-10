"""Market recording (T-87, RF-33, CA-19): what the hub applies goes to a file apart from
the journal; a write failure stops nothing."""

import stat

import pytest

from xbt_terminal.core.models import BookDelta, ConnState, D, Level, Trade
from xbt_terminal.services.faults import Faults
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.recording import (
    Recorder,
    RecordingError,
    read_recording,
    recording_path,
    scan_recording,
)

from ..fakes import T0, FakeFeed, book, spec


def hub_with(*venues):
    return MarketHub({v: FakeFeed(v) for v in venues}, clock=lambda: T0 + 5, faults=Faults())


def recorder(tmp_path, hub):
    path = tmp_path / "books.jsonl"
    return Recorder(hub, path, clock=lambda: T0 + 5), path


def load_market(hub, venue_id):
    state = hub.state[venue_id]
    state.spec, state.ready, state.loaded_at = spec(venue_id), True, T0
    hub._notify(venue_id, "market")


@pytest.mark.spec("RF-33", "CA-19")
class TestRecord:
    def test_notes_spec_and_events_with_time_and_venue(self, tmp_path):
        hub = hub_with("nk", "nx")
        rec, path = recorder(tmp_path, hub)
        load_market(hub, "nk")
        delta = BookDelta("nk", (Level(D("0.0072"), D("0")),), (), T0 + 1, seq=None)
        trade = Trade("nk", D("0.0073"), D("1"), "buy", T0 + 2)
        conn = ConnState("nx", "market", "ws", "up", 12.5, T0 + 3)
        snapshot = book("nk", "0.00720000", "0.00730000", ts=T0)
        for venue_id, event in (("nk", snapshot), ("nk", delta), ("nk", trade), ("nx", conn)):
            hub.apply(venue_id, event)
        rec.close()
        rows = list(read_recording(path))
        assert [(r.t, r.venue) for r in rows] == [(T0 + 5, v) for v in "nk nk nk nk nx".split()]
        assert [r.event for r in rows] == [spec("nk"), snapshot, delta, trade, conn]

    def test_file_is_user_only(self, tmp_path):
        rec, path = recorder(tmp_path, hub_with("nk"))
        rec.close()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_name_with_startup_utc_time(self, tmp_path):
        assert recording_path(tmp_path, 1_792_497_600_000).name == "books-20261020T120000Z.jsonl"

    def test_trade_dropped_by_hub_is_not_recorded(self, tmp_path):
        hub = hub_with("nk")
        rec, path = recorder(tmp_path, hub)
        hub.apply("nk", Trade("nk", D("0.0073"), D("1"), "buy", T0))  # market not loaded
        rec.close()
        assert list(read_recording(path)) == []

    def test_write_failure_stops_recording_only(self, tmp_path):
        hub = hub_with("nk")
        rec, _ = recorder(tmp_path, hub)

        class Full:
            def write(self, data):
                raise OSError("disco lleno")

            def close(self):
                raise OSError("disco lleno")

        rec._file = Full()
        snapshot = book("nk", "0.00720000", "0.00730000", ts=T0)
        hub.apply("nk", snapshot)
        hub.apply("nk", snapshot)
        assert hub.state["nk"].book.valid
        assert hub.faults.count == 1
        assert hub.faults.recent[0].where == "recording"
        rec.close()

    def test_after_closing_does_not_write(self, tmp_path):
        hub = hub_with("nk")
        rec, path = recorder(tmp_path, hub)
        rec.close()
        hub.apply("nk", book("nk", "0.00720000", "0.00730000", ts=T0))
        assert hub.faults.count == 0
        assert list(read_recording(path)) == []


def recorded(tmp_path, *venues):
    hub = hub_with(*venues)
    rec, path = recorder(tmp_path, hub)
    for venue_id in venues:
        load_market(hub, venue_id)
        hub.apply(venue_id, book(venue_id, "0.00720000", "0.00730000", ts=T0))
    rec.close()
    return path


@pytest.mark.spec("RF-34", "CA-19")
class TestRead:
    def test_scan_gives_start_end_and_specs(self, tmp_path):
        info = scan_recording(recorded(tmp_path, "nk", "nx"))
        assert (info.first, info.last) == (T0 + 5, T0 + 5)
        assert info.specs == {"nk": spec("nk"), "nx": spec("nx")}

    def test_one_venue_missing_is_invalid(self, tmp_path):
        with pytest.raises(RecordingError, match="nx"):
            scan_recording(recorded(tmp_path, "nk"))

    def test_bad_line_says_which(self, tmp_path):
        path = recorded(tmp_path, "nk", "nx")
        with path.open("a") as out:
            out.write('{"t": 1, "venue": "nk", "kind": "book"}\n')
        with pytest.raises(RecordingError, match=":6"):
            scan_recording(path)

    def test_other_format_not_read(self, tmp_path):
        path = tmp_path / "x.jsonl"
        path.write_text('{"format":2}\n')
        with pytest.raises(RecordingError, match="formato"):
            scan_recording(path)

    def test_missing_file_is_explained(self, tmp_path):
        with pytest.raises(RecordingError, match="no se puede leer"):
            scan_recording(tmp_path / "nada.jsonl")
