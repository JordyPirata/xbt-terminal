"""The daemon scans the self-custody wallets (T-103, RF-38, CA-22, D-84): gap limit on
both branches, a failed query keeps the previous balance, and an old one stops counting."""

import asyncio
from datetime import UTC, datetime

import pytest

from xbt_terminal.adapters.explorer import AddressStats, ExplorerError
from xbt_terminal.core.inventory import CustodyBalance
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import D
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.core.wallet import CHANGE, RECEIVE, address, parse_account_key
from xbt_terminal.services.custody import CustodyService, derive
from xbt_terminal.services.faults import Faults

from ..unit.test_wallet import BIP32_PARENT, BIP84_RECEIVE_0, BIP84_ZPUB

T0 = int(datetime(2026, 11, 14, 12, 0, tzinfo=UTC).timestamp() * 1000)
EVERY_S = 600
FIRST = parse_account_key(BIP84_ZPUB)
SECOND = parse_account_key(BIP32_PARENT)
NAMES = {FIRST: "first", SECOND: "second"}


def fake_address(key, branch, index):
    return f"{NAMES[key]}/{branch}/{index}"


class FakeExplorer:
    def __init__(self, funded=None):
        self.funded = dict(funded or {})  # address -> balance; listed means used
        self.asked: list[str] = []
        self.failing: set[str] = set()

    async def address(self, address):
        self.asked.append(address)
        if address in self.failing:
            raise ExplorerError("el explorador respondió HTTP 503")
        if address in self.funded:
            return AddressStats(D(self.funded[address]), True)
        return AddressStats(D("0"), False)


class Rig:
    def __init__(self, tmp_path, wallets, explorers, *, gap_limit=3, stop_after=2, **kwargs):
        self.now = T0
        self.dir = tmp_path
        self.journal = Journal(
            tmp_path, "live", Redactor([]),
            clock=lambda: datetime.fromtimestamp(self.now / 1000, UTC),
        )  # fmt: skip
        self.faults = Faults(self.journal)
        self.slept: list[float] = []
        self.stop_after = stop_after
        kwargs.setdefault("derive", fake_address)
        kwargs.setdefault("request_every_ms", 0)
        self.service = CustodyService(
            wallets, explorers, every_s=EVERY_S, gap_limit=gap_limit,
            clock=lambda: self.now, sleep=self.sleep, faults=self.faults, **kwargs,
        )  # fmt: skip

    async def sleep(self, seconds):
        self.slept.append(seconds)
        if len(self.slept) >= self.stop_after:
            raise asyncio.CancelledError
        self.now += round(seconds * 1000)

    def journal_text(self):
        return "".join(path.read_text() for path in sorted(self.dir.glob("journal-*.jsonl")))


@pytest.mark.spec("RF-38", "CA-22")
class TestScan:
    async def test_adds_up_used_addresses_of_both_branches(self, tmp_path):
        explorer = FakeExplorer(
            {"first/0/0": "0.5", "first/0/1": "0", "first/0/3": "0.25", "first/1/0": "0.001"}
        )
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer})
        assert await rig.service.scan() == CustodyBalance(D("0.751"), D("0"), T0)

    async def test_stops_after_gap_limit_unused_in_a_row(self, tmp_path):
        explorer = FakeExplorer({"first/0/0": "1", "first/0/3": "1"})
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer})
        await rig.service.scan()
        receive = [f"first/0/{i}" for i in range(7)]  # 3 is used: three more after it
        change = [f"first/1/{i}" for i in range(3)]
        assert explorer.asked == receive + change

    async def test_funds_beyond_the_gap_are_not_seen(self, tmp_path):
        explorer = FakeExplorer({"first/0/3": "1"})
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer})
        assert (await rig.service.scan()).btc == 0

    async def test_several_wallets_and_both_chains(self, tmp_path):
        btc = FakeExplorer({"first/0/0": "0.1"})
        btcb2 = FakeExplorer({"first/0/0": "3", "second/1/0": "4.5"})
        rig = Rig(
            tmp_path, {"BTC": (FIRST,), "BTCB2": (FIRST, SECOND)}, {"BTC": btc, "BTCB2": btcb2}
        )
        assert await rig.service.scan() == CustodyBalance(D("0.1"), D("7.5"), T0)
        assert not any(asked.startswith("second") for asked in btc.asked)

    async def test_failure_names_the_chain_as_the_screen_does(self, tmp_path):
        explorer = FakeExplorer()
        explorer.failing.add("first/0/0")
        rig = Rig(tmp_path, {"BTCB2": (FIRST,)}, {"BTCB2": explorer})
        with pytest.raises(ExplorerError, match=r"^XBT: "):
            await rig.service.scan()

    async def test_both_chains_are_scanned_at_once(self, tmp_path):
        both_asked = asyncio.Event()

        class Waiting(FakeExplorer):
            async def address(self, address):
                result = await super().address(address)
                if btc.asked and btcb2.asked:
                    both_asked.set()
                # sequential chains would never get here: the other one is not asked yet
                await asyncio.wait_for(both_asked.wait(), 1)
                return result

        btc, btcb2 = Waiting({"first/0/0": "1"}), Waiting({"first/0/0": "2"})
        rig = Rig(
            tmp_path, {"BTC": (FIRST,), "BTCB2": (FIRST,)}, {"BTC": btc, "BTCB2": btcb2}
        )
        assert await rig.service.scan() == CustodyBalance(D("1"), D("2"), T0)

    async def test_one_chain_failing_fails_the_scan(self, tmp_path):
        btc, btcb2 = FakeExplorer({"first/0/0": "1"}), FakeExplorer({"first/0/0": "2"})
        btcb2.failing.add("first/1/1")
        rig = Rig(
            tmp_path, {"BTC": (FIRST,), "BTCB2": (FIRST,)}, {"BTC": btc, "BTCB2": btcb2}
        )
        with pytest.raises(ExplorerError, match=r"^XBT: "):
            await rig.service.scan()
        assert rig.service.last is None

    async def test_chain_without_wallets_is_zero(self, tmp_path):
        rig = Rig(tmp_path, {"BTCB2": (FIRST,)}, {"BTCB2": FakeExplorer({"first/0/0": "2"})})
        assert await rig.service.scan() == CustodyBalance(D("0"), D("2"), T0)

    async def test_addresses_are_derived_once(self, tmp_path):
        calls = []

        def counting(key, branch, index):
            calls.append((branch, index))
            return fake_address(key, branch, index)

        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": FakeExplorer()}, derive=counting)
        await rig.service.scan()
        await rig.service.scan()
        assert len(calls) == len(set(calls)) == 6

    @pytest.mark.spec("RF-37")
    async def test_real_derivation_is_native_segwit(self, tmp_path):
        explorer = FakeExplorer({BIP84_RECEIVE_0: "0.3"})
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer}, gap_limit=2, derive=derive)
        assert (await rig.service.scan()).btc == D("0.3")
        assert all(asked.startswith("bc1q") for asked in explorer.asked)
        assert derive(FIRST, CHANGE, 1) == address(FIRST, CHANGE, 1)
        assert derive(FIRST, RECEIVE, 0) == BIP84_RECEIVE_0


@pytest.mark.spec("RF-38", "CA-22")
class TestFailuresAndAge:
    async def test_no_balance_before_the_first_scan(self, tmp_path):
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": FakeExplorer()})
        assert rig.service.balance() is None

    async def test_failed_query_keeps_the_previous_balance(self, tmp_path):
        explorer = FakeExplorer({"first/0/0": "1", "first/1/0": "2"})
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer})
        good = await rig.service.scan()
        rig.now += EVERY_S * 1000
        explorer.failing.add("first/1/0")
        with pytest.raises(ExplorerError, match=r"^BTC: el explorador respondió HTTP 503$"):
            await rig.service.scan()
        assert rig.service.balance() == good == CustodyBalance(D("3"), D("0"), T0)

    async def test_old_balance_stops_counting(self, tmp_path):
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": FakeExplorer({"first/0/0": "1"})})
        await rig.service.scan()
        rig.now += 3 * EVERY_S * 1000
        assert rig.service.balance() is not None
        rig.now += 1
        assert rig.service.balance() is None

    async def test_run_scans_at_startup_and_on_each_interval(self, tmp_path):
        explorer = FakeExplorer({"first/0/0": "1"})
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer}, stop_after=3)
        with pytest.raises(asyncio.CancelledError):
            await rig.service.run()
        assert rig.slept == [EVERY_S] * 3
        assert explorer.asked.count("first/0/0") == 3
        assert rig.service.last.at == T0 + 2 * EVERY_S * 1000

    @pytest.mark.spec("RNF-14")
    async def test_run_reports_a_failed_scan_and_goes_on(self, tmp_path):
        explorer = FakeExplorer({"first/0/0": "1"})
        explorer.failing.add("first/0/0")
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer}, stop_after=2)
        with pytest.raises(asyncio.CancelledError):
            await rig.service.run()
        assert rig.faults.count == 2 and rig.faults.recent[-1].where == "custody"
        assert rig.service.balance() is None

    @pytest.mark.spec("RNF-25")
    async def test_fault_and_journal_never_carry_an_address(self, tmp_path):
        explorer = FakeExplorer()
        explorer.failing.add(BIP84_RECEIVE_0)
        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer}, stop_after=1, derive=derive)
        with pytest.raises(asyncio.CancelledError):
            await rig.service.run()
        assert rig.faults.count == 1
        written = rig.journal_text() + repr(rig.faults.recent)
        assert "custody" in written
        assert BIP84_RECEIVE_0 not in written and BIP84_ZPUB not in written

    async def test_pauses_before_each_query(self, tmp_path):
        paused = []

        async def pause(seconds):
            paused.append(seconds)

        explorer = FakeExplorer({"first/0/0": "1"})
        rig = Rig(
            tmp_path, {"BTC": (FIRST,)}, {"BTC": explorer}, request_every_ms=250, pause=pause
        )
        await rig.service.scan()
        assert paused == [0.25] * len(explorer.asked) and len(paused) == 7
        assert rig.slept == []  # not the sleep between scans

    async def test_closing_closes_the_http_client(self, tmp_path):
        closed = []

        async def close():
            closed.append(True)

        rig = Rig(tmp_path, {"BTC": (FIRST,)}, {"BTC": FakeExplorer()}, close=close)
        await rig.service.aclose()
        assert closed == [True]


# -- wiring: engine, socket and client (T-103) -------------------------------------------


class FakeCustody:
    def __init__(self, balance):
        self._balance = balance
        self.closed = False

    def balance(self):
        return self._balance

    async def run(self):
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.mark.spec("RF-39", "RNF-25", "CA-22")
class TestSocket:
    async def test_client_receives_the_balance_and_nothing_else(self, tmp_path):
        from xbt_terminal.services.remote import RemoteEngine
        from xbt_terminal.services.wire import dumps, encode, snapshot

        from .test_remote import Daemon, until

        async with Daemon(tmp_path) as daemon:
            balance = CustodyBalance(D("0.5"), D("12"), T0)
            daemon.engine.custody = FakeCustody(balance)
            remote = RemoteEngine(daemon.path, has_bot=True, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.account.has_custody)
            assert remote.account.custody == balance
            sent = dumps({"type": "state", "state": encode(snapshot(daemon.engine))}).decode()
            assert '"custody":{"btc":"0.5","btcb2":"12"' in sent
            assert "pub" not in sent and "bc1q" not in sent
            daemon.engine.custody = FakeCustody(None)  # the scan got too old
            await until(lambda: remote.account.custody is None)
            assert remote.account.has_custody
            task.cancel()
            await remote.aclose()

    async def test_without_wallets(self, tmp_path):
        from xbt_terminal.services.wire import snapshot

        from .test_remote import Daemon

        async with Daemon(tmp_path) as daemon:
            state = snapshot(daemon.engine)
            assert (state.has_custody, state.custody) == (False, None)


@pytest.mark.spec("RF-30", "RF-38")
class TestEngine:
    async def test_runs_and_closes_the_custody_service(self, tmp_path):
        from .test_remote import Daemon

        daemon = Daemon(tmp_path, bot=False)
        assert "custody" not in dict(daemon.engine.services())
        custody = FakeCustody(None)
        daemon.engine.custody = custody
        assert dict(daemon.engine.services())["custody"] == custody.run
        await daemon.engine.aclose()
        assert custody.closed
