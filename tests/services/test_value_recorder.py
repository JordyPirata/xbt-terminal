"""El daemon muestrea el valor de la cuenta (T-69, RF-32, CA-17, D-69): un
evento `value` en cada múltiplo UTC del intervalo, y ninguno si falta un mid o
un saldo."""

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
        self.now = T0 + 12_345  # entre dos múltiplos
        self.account = SimpleNamespace(
            state={
                "nk": SimpleNamespace(balances=bal("0.1", "2")),
                "nx": SimpleNamespace(balances=bal("0.2", "4") if nx_bal else None),
            }
        )
        mids = {"nk": D("0.005"), "nx": D(nx_mid) if nx_mid else None}
        self.hub = SimpleNamespace(
            state={vid: SimpleNamespace(book=SimpleNamespace(mid=m)) for vid, m in mids.items()}
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
        for p in sorted(self.dir.glob("journal-*.jsonl")):
            lines += p.read_text().splitlines()
        return read_rows(lines)[0]


@pytest.mark.spec("RF-32", "CA-17")
class TestRecorder:
    async def test_en_cada_multiplo_del_intervalo(self, tmp_path):
        rig = Rig(tmp_path)
        with pytest.raises(asyncio.CancelledError):
            await rig.recorder.run()
        got, bad = series(rig.rows(), None)
        assert bad == 0
        assert [s.ts for s in got] == [T0 + k * EVERY_S * 1000 for k in (1, 2, 3)]
        assert rig.slept[0] == pytest.approx(EVERY_S - 12.345)
        assert rig.slept[1] == pytest.approx(EVERY_S)
        s = got[0]
        assert s.value == D("0.334") and s.every_ms == EVERY_S * 1000
        assert [v.venue for v in s.venues] == ["nk", "nx"]

    async def test_el_modo_va_en_la_fila(self, tmp_path):
        rig = Rig(tmp_path)
        rig.recorder.record(T0)
        (row,) = rig.rows()
        assert row["event"] == "value" and row["mode"] == "live"
        assert json.dumps(row["data"]["value"]) == '"0.334"'  # Decimal como string

    async def test_sin_mid_se_salta(self, tmp_path):
        rig = Rig(tmp_path, nx_mid=None)
        assert rig.recorder.record(T0) is None
        assert rig.rows() == []

    async def test_sin_saldo_se_salta(self, tmp_path):
        rig = Rig(tmp_path, nx_bal=False)
        assert rig.recorder.record(T0) is None
        assert rig.rows() == []

    async def test_un_fallo_no_para_el_muestreo(self, tmp_path):
        rig = Rig(tmp_path)
        calls = []

        def boom(at):
            calls.append(at)
            raise RuntimeError("bug")

        rig.recorder.record = boom
        with pytest.raises(asyncio.CancelledError):
            await rig.recorder.run()
        assert len(calls) == 3
        assert any(f.where == "value" for f in rig.faults.recent)
