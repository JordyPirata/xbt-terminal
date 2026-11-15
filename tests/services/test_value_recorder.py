"""The daemon samples the account value (T-69, RF-32, CA-17, D-69): one `value` event on
each UTC multiple of the interval, and none if a mid or balance is missing."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, D
from xbt_terminal.core.results import read_rows
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.core.valuation import series
from xbt_terminal.services.faults import Faults
from xbt_terminal.services.valuation import ValueRecorder

T0 = int(datetime(2026, 10, 25, 12, 0, tzinfo=UTC).timestamp() * 1000)
EVERY_S = 300


def bal(btc, btcb2):
    return {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D(btcb2))}


class Rig:
    def __init__(self, tmp_path, *, nx_mid="0.006", nx_bal=True):
        self.now = T0 + 12_345  # between two multiples
        self.account = SimpleNamespace(
            state={
                "nk": SimpleNamespace(balances=bal("0.1", "2")),
                "nx": SimpleNamespace(balances=bal("0.2", "4") if nx_bal else None),
            }
        )
        mids = {"nk": D("0.005"), "nx": D(nx_mid) if nx_mid else None}
        self.hub = SimpleNamespace(
            state={
                venue_id: SimpleNamespace(book=SimpleNamespace(mid=mid_price))
                for venue_id, mid_price in mids.items()
            }
        )
        self.dir = tmp_path
        self.journal = Journal(
            tmp_path, "live", Redactor([]),
            clock=lambda: datetime.fromtimestamp(self.now / 1000, UTC),
        )  # fmt: skip
        self.faults = Faults(self.journal)
        self.slept: list[float] = []
        self.recorder = ValueRecorder(
            self.account, self.hub, self.journal, every_s=EVERY_S,
            clock=lambda: self.now, sleep=self.sleep, faults=self.faults,
        )  # fmt: skip

    async def sleep(self, seconds):
        self.slept.append(seconds)
        if len(self.slept) > 3:
            raise asyncio.CancelledError
        self.now += round(seconds * 1000)

    def rows(self):
        lines = []
        for path in sorted(self.dir.glob("journal-*.jsonl")):
            lines += path.read_text().splitlines()
        return read_rows(lines)[0]


@pytest.mark.spec("RF-32", "CA-17")
class TestRecorder:
    async def test_on_each_interval_multiple(self, tmp_path):
        rig = Rig(tmp_path)
        with pytest.raises(asyncio.CancelledError):
            await rig.recorder.run()
        got, bad = series(rig.rows(), None)
        assert bad == 0
        assert [value_sample.ts for value_sample in got] == [
            T0 + k * EVERY_S * 1000 for k in (1, 2, 3)
        ]
        assert rig.slept[0] == pytest.approx(EVERY_S - 12.345)
        assert rig.slept[1] == pytest.approx(EVERY_S)
        value_sample = got[0]
        assert value_sample.value == D("0.334") and value_sample.every_ms == EVERY_S * 1000
        assert [venue.venue for venue in value_sample.venues] == ["nk", "nx"]

    async def test_mode_goes_in_the_row(self, tmp_path):
        rig = Rig(tmp_path)
        rig.recorder.record(T0)
        (row,) = rig.rows()
        assert row["event"] == "value" and row["mode"] == "live"
        assert json.dumps(row["data"]["value"]) == '"0.334"'

    async def test_no_mid_is_skipped(self, tmp_path):
        rig = Rig(tmp_path, nx_mid=None)
        assert rig.recorder.record(T0) is None
        assert rig.rows() == []

    async def test_no_balance_is_skipped(self, tmp_path):
        rig = Rig(tmp_path, nx_bal=False)
        assert rig.recorder.record(T0) is None
        assert rig.rows() == []

    async def test_failure_does_not_stop_sampling(self, tmp_path):
        rig = Rig(tmp_path)
        calls = []

        def boom(at):
            calls.append(at)
            raise RuntimeError("bug")

        rig.recorder.record = boom
        with pytest.raises(asyncio.CancelledError):
            await rig.recorder.run()
        assert len(calls) == 3
        assert any(fault.where == "value" for fault in rig.faults.recent)


# -- self-custody in the sample (T-105, RF-39) -------------------------------------------


class Wallets:
    def __init__(self, balance):
        self.current = balance

    def balance(self):
        return self.current


def custody_rig(tmp_path, balance):
    from xbt_terminal.core.inventory import CustodyBalance

    rig = Rig(tmp_path)
    wallets = Wallets(CustodyBalance(D(balance[0]), D(balance[1]), T0) if balance else None)
    rig.recorder = ValueRecorder(
        rig.account, rig.hub, rig.journal, every_s=EVERY_S,
        clock=lambda: rig.now, sleep=rig.sleep, faults=rig.faults, custody=wallets,
    )  # fmt: skip
    return rig, wallets


@pytest.mark.spec("RF-39", "RF-32", "CA-22")
class TestCustodyInTheSample:
    def test_total_includes_the_wallets(self, tmp_path):
        from xbt_terminal.core.valuation import CustodyValue

        rig, _ = custody_rig(tmp_path, ("1", "10"))
        value_sample = rig.recorder.record(T0)
        # venues are worth 0.334; the wallet, 1 + 10 × 0.0055 (mean of 0.005 and 0.006)
        assert value_sample.custody == CustodyValue(D("1"), D("10"), D("0.0055"), D("1.055"))
        assert value_sample.value == D("1.389")
        (row,) = rig.rows()
        assert row["data"]["custody"] == {
            "btc": "1", "btcb2": "10", "mid": "0.0055", "value": "1.0550",
        }  # fmt: skip
        (read,), bad = series(rig.rows(), None)
        assert bad == 0 and read == value_sample

    def test_without_a_valid_wallet_balance_nothing_is_written(self, tmp_path):
        rig, wallets = custody_rig(tmp_path, None)
        assert rig.recorder.record(T0) is None and rig.rows() == []
        from xbt_terminal.core.inventory import CustodyBalance

        wallets.current = CustodyBalance(D("0"), D("0"), T0)
        assert rig.recorder.record(T0 + EVERY_S * 1000).value == D("0.334")

    def test_without_wallets_the_row_is_as_before(self, tmp_path):
        rig = Rig(tmp_path)
        assert rig.recorder.record(T0).custody is None
        (row,) = rig.rows()
        assert "custody" not in row["data"]

    def test_old_rows_and_new_ones_read_together(self, tmp_path):
        from xbt_terminal.core.valuation import last_stretch

        rig = Rig(tmp_path)
        rig.recorder.record(T0)
        with_wallets, _ = custody_rig(tmp_path, ("1", "10"))
        with_wallets.now = rig.now
        with_wallets.recorder.record(T0 + EVERY_S * 1000)
        got, bad = series(with_wallets.rows(), None)
        assert bad == 0 and [value_sample.custody is None for value_sample in got] == [True, False]
        # adding the wallets is a jump of the total: the last stretch starts there
        assert last_stretch(got) == got[1:]

    def test_a_move_inside_the_wallets_starts_a_new_stretch(self, tmp_path):
        from dataclasses import replace

        from xbt_terminal.core.valuation import last_stretch

        rig, _ = custody_rig(tmp_path, ("0.001", "0"))
        first = rig.recorder.record(T0)
        moved = replace(first.custody, btc=D("0.0012"), value=D("0.0012"))
        second = replace(first, ts=T0 + EVERY_S * 1000, custody=moved)
        assert last_stretch((first, second)) == (second,)
