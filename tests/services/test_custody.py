"""The daemon scans the watched addresses (T-103, T-108, RF-37, RF-38, CA-22, D-85): the
list persists, a failed query keeps the previous balance, an old one stops counting, and
a change of the list drops the balance until a scan of the new list ends."""

import asyncio
import json
import os
import stat
from datetime import UTC, datetime

import pytest

from xbt_terminal.adapters.explorer import AddressStats, ExplorerError
from xbt_terminal.core.inventory import CustodyBalance
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import D
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.custody import MAX_ADDRESSES, CustodyService, WatchError, WatchList
from xbt_terminal.services.faults import Faults

T0 = int(datetime(2026, 11, 14, 12, 0, tzinfo=UTC).timestamp() * 1000)
EVERY_S = 600
# real addresses (BIP173 / BIP350 / base58 vectors): the list validates them
SEGWIT = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
TAPROOT = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
LEGACY = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
NESTED = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"


class FakeExplorer:
    def __init__(self, funded=None):
        self.funded = dict(funded or {})  # address -> balance
        self.asked: list[str] = []
        self.failing: set[str] = set()

    async def address(self, address):
        self.asked.append(address)
        if address in self.failing:
            raise ExplorerError("el explorador respondió HTTP 503")
        balance = D(self.funded.get(address, "0"))
        return AddressStats(balance, balance != 0)


class Rig:
    def __init__(self, tmp_path, watched, explorers, *, stop_after=2, **kwargs):
        self.now = T0
        self.dir = tmp_path
        self.journal = Journal(
            tmp_path, "live", Redactor([]),
            clock=lambda: datetime.fromtimestamp(self.now / 1000, UTC),
        )  # fmt: skip
        self.faults = Faults(self.journal)
        self.slept: list[float] = []
        self.stop_after = stop_after
        self.watchlist = WatchList(tmp_path / "watch.json")
        for asset, addresses in watched.items():
            for address in addresses:
                self.watchlist.add(asset, address)
        kwargs.setdefault("request_every_ms", 0)
        self.service = CustodyService(
            self.watchlist, explorers, every_s=EVERY_S, clock=lambda: self.now,
            sleep=self.sleep, faults=self.faults, **kwargs,
        )  # fmt: skip

    async def sleep(self, seconds):
        self.slept.append(seconds)
        if len(self.slept) >= self.stop_after:
            raise asyncio.CancelledError
        self.now += round(seconds * 1000)

    def journal_text(self):
        return "".join(path.read_text() for path in sorted(self.dir.glob("journal-*.jsonl")))


@pytest.mark.spec("RF-37", "RNF-25", "CA-22")
class TestWatchList:
    def test_add_remove_and_persist(self, tmp_path):
        path = tmp_path / "watch.json"
        watchlist = WatchList(path)
        watchlist.add("BTC", SEGWIT)
        watchlist.add("BTCB2", TAPROOT.upper())  # canonical form: lowercase
        watchlist.add("BTCB2", LEGACY)
        reloaded = WatchList(path)
        assert reloaded.addresses("BTC") == (SEGWIT,)
        assert reloaded.addresses("BTCB2") == (TAPROOT, LEGACY)
        reloaded.remove("BTCB2", TAPROOT)
        assert WatchList(path).addresses("BTCB2") == (LEGACY,)

    def test_file_is_private(self, tmp_path):
        WatchList(tmp_path / "watch.json").add("BTC", SEGWIT)
        assert stat.S_IMODE(os.stat(tmp_path / "watch.json").st_mode) == 0o600
        assert not (tmp_path / "watch.json.tmp").exists()

    def test_empty_without_a_file(self, tmp_path):
        watchlist = WatchList(tmp_path / "watch.json")
        assert not watchlist.any() and watchlist.addresses("BTC") == ()

    def test_same_address_may_be_on_both_chains(self, tmp_path):
        watchlist = WatchList(tmp_path / "watch.json")
        watchlist.add("BTC", SEGWIT)
        watchlist.add("BTCB2", SEGWIT)
        assert watchlist.addresses("BTC") == watchlist.addresses("BTCB2") == (SEGWIT,)

    @pytest.mark.parametrize(
        ("action", "address", "message"),
        [
            ("add", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5", "checksum"),
            ("add", SEGWIT, "ya se vigila en BTC"),
            ("remove", LEGACY, "no se vigila en BTC"),
        ],
    )
    def test_refused(self, tmp_path, action, address, message):
        watchlist = WatchList(tmp_path / "watch.json")
        watchlist.add("BTC", SEGWIT)
        with pytest.raises(WatchError, match=message) as raised:
            getattr(watchlist, action)("BTC", address)
        assert address not in str(raised.value)
        assert watchlist.addresses("BTC") == (SEGWIT,)

    def test_the_address_after_the_limit_is_refused(self, tmp_path):
        watchlist = WatchList(tmp_path / "watch.json")
        # not real addresses on disk: the limit is what is under test
        watchlist._addresses["BTC"] = [f"x{i}" for i in range(MAX_ADDRESSES)]
        with pytest.raises(WatchError, match="máximo 50"):
            watchlist.add("BTC", SEGWIT)

    def test_corrupt_file_does_not_start_and_does_not_echo_it(self, tmp_path):
        (tmp_path / "watch.json").write_text(json.dumps({"BTC": [SEGWIT, "not-an-address"]}))
        with pytest.raises(WatchError, match=r"watch\.json") as raised:
            WatchList(tmp_path / "watch.json")
        assert SEGWIT not in str(raised.value)
        (tmp_path / "watch.json").write_text("{")
        with pytest.raises(WatchError):
            WatchList(tmp_path / "watch.json")


@pytest.mark.spec("RF-38", "CA-22")
class TestScan:
    async def test_adds_up_the_watched_addresses(self, tmp_path):
        explorer = FakeExplorer({SEGWIT: "0.5", TAPROOT: "0.001", LEGACY: "9"})
        rig = Rig(tmp_path, {"BTC": (SEGWIT, TAPROOT)}, {"BTC": explorer})
        assert await rig.service.scan() == CustodyBalance(D("0.501"), D("0"), T0)
        assert explorer.asked == [SEGWIT, TAPROOT]

    async def test_both_chains(self, tmp_path):
        btc, btcb2 = FakeExplorer({SEGWIT: "0.1"}), FakeExplorer({SEGWIT: "3", TAPROOT: "4.5"})
        watched = {"BTC": (SEGWIT,), "BTCB2": (SEGWIT, TAPROOT)}
        rig = Rig(tmp_path, watched, {"BTC": btc, "BTCB2": btcb2})
        assert await rig.service.scan() == CustodyBalance(D("0.1"), D("7.5"), T0)
        assert TAPROOT not in btc.asked

    async def test_chain_without_addresses_is_zero_and_not_asked(self, tmp_path):
        btc = FakeExplorer({SEGWIT: "1"})
        btcb2 = FakeExplorer({SEGWIT: "2"})
        rig = Rig(tmp_path, {"BTCB2": (SEGWIT,)}, {"BTC": btc, "BTCB2": btcb2})
        assert await rig.service.scan() == CustodyBalance(D("0"), D("2"), T0)
        assert btc.asked == []

    async def test_nothing_watched_means_no_balance(self, tmp_path):
        rig = Rig(tmp_path, {}, {"BTC": FakeExplorer()})
        assert await rig.service.scan() is None
        assert not rig.service.active and rig.service.balance() is None

    async def test_failure_names_the_chain_as_the_screen_does(self, tmp_path):
        explorer = FakeExplorer()
        explorer.failing.add(SEGWIT)
        rig = Rig(tmp_path, {"BTCB2": (SEGWIT,)}, {"BTCB2": explorer})
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

        btc, btcb2 = Waiting({SEGWIT: "1"}), Waiting({SEGWIT: "2"})
        rig = Rig(tmp_path, {"BTC": (SEGWIT,), "BTCB2": (SEGWIT,)}, {"BTC": btc, "BTCB2": btcb2})
        assert await rig.service.scan() == CustodyBalance(D("1"), D("2"), T0)

    async def test_one_chain_failing_fails_the_scan(self, tmp_path):
        btc, btcb2 = FakeExplorer({SEGWIT: "1"}), FakeExplorer({SEGWIT: "2"})
        btcb2.failing.add(SEGWIT)
        rig = Rig(tmp_path, {"BTC": (SEGWIT,), "BTCB2": (SEGWIT,)}, {"BTC": btc, "BTCB2": btcb2})
        with pytest.raises(ExplorerError, match=r"^XBT: "):
            await rig.service.scan()
        assert rig.service.last is None


@pytest.mark.spec("RF-38", "CA-22")
class TestFailuresAndAge:
    async def test_no_balance_before_the_first_scan(self, tmp_path):
        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": FakeExplorer()})
        assert rig.service.balance() is None

    async def test_failed_query_keeps_the_previous_balance(self, tmp_path):
        explorer = FakeExplorer({SEGWIT: "1", TAPROOT: "2"})
        rig = Rig(tmp_path, {"BTC": (SEGWIT, TAPROOT)}, {"BTC": explorer})
        good = await rig.service.scan()
        rig.now += EVERY_S * 1000
        explorer.failing.add(TAPROOT)
        with pytest.raises(ExplorerError, match=r"^BTC: el explorador respondió HTTP 503$"):
            await rig.service.scan()
        assert rig.service.balance() == good == CustodyBalance(D("3"), D("0"), T0)

    async def test_old_balance_stops_counting(self, tmp_path):
        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": FakeExplorer({SEGWIT: "1"})})
        await rig.service.scan()
        rig.now += 3 * EVERY_S * 1000
        assert rig.service.balance() is not None
        rig.now += 1
        assert rig.service.balance() is None

    async def test_run_scans_at_startup_and_on_each_interval(self, tmp_path):
        explorer = FakeExplorer({SEGWIT: "1"})
        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": explorer}, stop_after=3)
        with pytest.raises(asyncio.CancelledError):
            await rig.service.run()
        assert rig.slept == [EVERY_S] * 3
        assert explorer.asked.count(SEGWIT) == 3
        assert rig.service.last.at == T0 + 2 * EVERY_S * 1000

    @pytest.mark.spec("RNF-14")
    async def test_run_reports_a_failed_scan_and_goes_on(self, tmp_path):
        explorer = FakeExplorer({SEGWIT: "1"})
        explorer.failing.add(SEGWIT)
        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": explorer}, stop_after=2)
        with pytest.raises(asyncio.CancelledError):
            await rig.service.run()
        assert rig.faults.count == 2 and rig.faults.recent[-1].where == "custody"
        assert rig.service.balance() is None

    @pytest.mark.spec("RNF-25")
    async def test_fault_and_journal_never_carry_an_address(self, tmp_path):
        explorer = FakeExplorer()
        explorer.failing.add(SEGWIT)
        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": explorer}, stop_after=1)
        with pytest.raises(asyncio.CancelledError):
            await rig.service.run()
        assert rig.faults.count == 1
        written = rig.journal_text() + repr(rig.faults.recent)
        assert "custody" in written and SEGWIT not in written

    async def test_pauses_before_each_query(self, tmp_path):
        paused = []

        async def pause(seconds):
            paused.append(seconds)

        explorer = FakeExplorer({SEGWIT: "1"})
        rig = Rig(
            tmp_path, {"BTC": (SEGWIT, TAPROOT)}, {"BTC": explorer},
            request_every_ms=250, pause=pause,
        )
        await rig.service.scan()
        assert paused == [0.25, 0.25]
        assert rig.slept == []  # not the sleep between scans

    async def test_closing_closes_the_http_client(self, tmp_path):
        closed = []

        async def close():
            closed.append(True)

        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": FakeExplorer()}, close=close)
        await rig.service.aclose()
        assert closed == [True]


@pytest.mark.spec("RF-37", "RF-38", "CA-22")
class TestChangingTheList:
    async def test_add_drops_the_balance_until_a_new_scan(self, tmp_path):
        explorer = FakeExplorer({SEGWIT: "1", TAPROOT: "2"})
        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": explorer})
        await rig.service.scan()
        assert rig.service.balance() is not None
        rig.service.add("BTC", TAPROOT)
        assert rig.service.balance() is None and rig.service.active
        assert (await rig.service.scan()).btc == D("3")

    async def test_remove_drops_the_balance_too(self, tmp_path):
        explorer = FakeExplorer({SEGWIT: "1", TAPROOT: "2"})
        rig = Rig(tmp_path, {"BTC": (SEGWIT, TAPROOT)}, {"BTC": explorer})
        await rig.service.scan()
        rig.service.remove("BTC", TAPROOT)
        assert rig.service.balance() is None
        assert (await rig.service.scan()).btc == D("1")

    async def test_a_scan_of_the_old_list_is_discarded(self, tmp_path):
        class Changing(FakeExplorer):
            async def address(self, address):
                result = await super().address(address)
                if address == SEGWIT:
                    rig.service.add("BTC", TAPROOT)  # changes while the scan is running
                return result

        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": Changing({SEGWIT: "1", TAPROOT: "2"})})
        assert await rig.service.scan() is None
        assert rig.service.balance() is None

    async def test_a_change_wakes_the_loop_without_waiting_the_interval(self, tmp_path):
        explorer = FakeExplorer({SEGWIT: "1", TAPROOT: "2"})
        rig = Rig(tmp_path, {"BTC": (SEGWIT,)}, {"BTC": explorer})
        release = asyncio.Event()

        async def long_sleep(seconds):
            await release.wait()

        rig.service._sleep = long_sleep
        task = asyncio.create_task(rig.service.run())
        for _ in range(50):
            await asyncio.sleep(0)
        assert rig.service.balance().btc == D("1")
        rig.service.add("BTC", TAPROOT)
        for _ in range(50):
            await asyncio.sleep(0)
        assert rig.service.balance().btc == D("3")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_chain_without_url_refuses_the_address(self, tmp_path):
        rig = Rig(tmp_path, {}, {"BTC": FakeExplorer()})
        with pytest.raises(WatchError, match=r"falta la url de XBT"):
            rig.service.add("BTCB2", SEGWIT)
        assert not rig.service.active


# -- wiring: engine, socket and client (T-103, T-108) -------------------------------------------


class FakeCustody:
    active = True

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


@pytest.mark.spec("RF-37", "RNF-25", "CA-22")
class TestWatchCommand:
    """`watch` over the socket: the addresses travel only in its reply (RNF-25)."""

    async def _daemon(self, tmp_path):
        from .test_remote import Daemon

        daemon = Daemon(tmp_path, bot=False)
        explorer = FakeExplorer({SEGWIT: "0.5"})
        daemon.engine.custody = CustodyService(
            WatchList(tmp_path / "watch.json"), {"BTC": explorer}, request_every_ms=0
        )
        return daemon

    async def test_add_list_remove(self, tmp_path):
        from xbt_terminal.services.remote import RemoteEngine

        from .test_remote import until

        daemon = await self._daemon(tmp_path)
        async with daemon:
            remote = RemoteEngine(daemon.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.connected and remote.snapshot is not None)
            assert not remote.snapshot.has_custody
            added = await remote.watch.send("add", "BTC", SEGWIT)
            assert added.btc == (SEGWIT,) and added.xbt == ()
            await until(lambda: remote.snapshot.has_custody)
            assert (await remote.watch.send("list")).btc == (SEGWIT,)
            removed = await remote.watch.send("rm", "BTC", SEGWIT)
            assert removed.btc == ()
            await until(lambda: not remote.snapshot.has_custody)
            task.cancel()
            await remote.aclose()

    async def test_refusals_reach_the_client_without_the_address(self, tmp_path):
        from xbt_terminal.services.remote import DaemonRefusedError, RemoteEngine

        from .test_remote import until

        daemon = await self._daemon(tmp_path)
        async with daemon:
            remote = RemoteEngine(daemon.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.connected)
            with pytest.raises(DaemonRefusedError, match="falta la url de XBT") as refused:
                await remote.watch.send("add", "BTCB2", SEGWIT)
            assert SEGWIT not in str(refused.value)
            with pytest.raises(DaemonRefusedError, match="checksum"):
                await remote.watch.send("add", "BTC", SEGWIT[:-1] + "5")
            with pytest.raises(DaemonRefusedError, match="no se vigila"):
                await remote.watch.send("rm", "BTC", SEGWIT)
            task.cancel()
            await remote.aclose()

    async def test_without_the_custody_section(self, tmp_path):
        from xbt_terminal.services.remote import DaemonRefusedError, RemoteEngine

        from .test_remote import Daemon, until

        async with Daemon(tmp_path, bot=False) as daemon:
            remote = RemoteEngine(daemon.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.connected)
            with pytest.raises(DaemonRefusedError, match=r"\[custody\]"):
                await remote.watch.send("list")
            task.cancel()
            await remote.aclose()

    async def test_the_address_is_nowhere_else(self, tmp_path):
        from xbt_terminal.services.remote import RemoteEngine
        from xbt_terminal.services.wire import dumps, encode, snapshot

        from .test_remote import until

        daemon = await self._daemon(tmp_path)
        async with daemon:
            remote = RemoteEngine(daemon.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.connected)
            await remote.watch.send("add", "BTC", SEGWIT)
            await until(lambda: remote.snapshot.has_custody)
            sent = dumps({"type": "state", "state": encode(snapshot(daemon.engine))}).decode()
            assert SEGWIT not in sent
            task.cancel()
            await remote.aclose()
