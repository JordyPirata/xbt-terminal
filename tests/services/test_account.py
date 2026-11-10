"""AccountService (T-17, design.md §6.5): each venue's private stream → channel
state, open orders, fills to the journal and balances refreshed every
`balances_every_s` and after each fill."""

import asyncio
import json

import httpx
import pytest

from xbt_terminal.adapters.base import ReadOnlyError, VenueError
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, ConnState, D, FeeUpdate, Fill, Order
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.account import AccountService

T0 = 1_792_497_600_000


def order(oid="o-1", status="open", filled="0", cid="t-abc123"):
    return Order("nk", oid, cid, "buy", D("0.0072"), D("0.01"), D(filled), status, T0)


def fill(tid="f-1", oid="o-1"):
    return Fill("nk", tid, oid, "buy", D("0.0072"), D("0.004"), D("0.00000006"), "BTC", T0 + 5)


class FakePrivate:
    """Fake private venue: the test pushes events and counts balance requests."""

    id = "nk"

    def __init__(self, fail_stream=None):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.stream_calls = 0
        self.balance_calls = 0
        self.fail_balances = False
        self.fail_stream = fail_stream

    def push(self, *events):
        for ev in events:
            self.queue.put_nowait(ev)

    async def balances(self):
        self.balance_calls += 1
        if isinstance(self.fail_balances, Exception):
            raise self.fail_balances
        if self.fail_balances:
            raise VenueError("sin red")
        return {"BTC": Balance("BTC", D("0.001")), "BTCB2": Balance("BTCB2", D(self.balance_calls))}

    async def private_stream(self):
        self.stream_calls += 1
        if self.fail_stream:
            raise self.fail_stream
        while True:
            event = await self.queue.get()
            if isinstance(event, Exception):  # cut a stream already open
                raise event
            yield event


class Rig:
    def __init__(self, tmp_path, every=60.0, **kw):
        self.venue = FakePrivate(**kw)
        self.logs = tmp_path / "logs"
        self.journal = Journal(self.logs, "live", Redactor([]))
        self.seen: list[tuple[str, str]] = []
        self.account = AccountService(
            {"nk": self.venue},
            journal=self.journal,
            balances_every_s=every,
            retry_s=0.01,
            clock=lambda: T0,
        )
        self.account.subscribe(lambda v, what: self.seen.append((v, what)))

    def lines(self):
        files = sorted(self.logs.glob("journal-*.jsonl"))
        return [json.loads(ln) for f in files for ln in f.read_text().splitlines()]

    async def __aenter__(self):
        self.task = asyncio.create_task(self.account.run())
        return self

    async def __aexit__(self, *error):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.journal.close()


async def until(predicate, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.spec("RF-14")
async def test_private_channel_state(tmp_path):
    async with Rig(tmp_path) as rig:
        rig.venue.push(ConnState("nk", "private", "ws", "up", 120.0, T0))
        state = rig.account.state["nk"]
        await until(lambda: state.conn is not None)
        assert (state.conn.source, state.conn.status, state.conn_at) == ("ws", "up", T0)
        assert ("nk", "conn") in rig.seen


@pytest.mark.spec("RF-08")
async def test_open_and_finished_orders(tmp_path):
    async with Rig(tmp_path) as rig:
        state = rig.account.state["nk"]
        rig.venue.push(order("a"), order("b"))
        await until(lambda: len(state.orders) == 2)
        rig.venue.push(order("a", status="cancelled"))
        await until(lambda: list(state.orders) == ["b"])
        assert ("nk", "orders") in rig.seen


@pytest.mark.spec("RF-05", "RF-09", "RF-11")
async def test_fill_to_journal_and_refreshes_balances(tmp_path):
    async with Rig(tmp_path) as rig:
        state = rig.account.state["nk"]
        await until(lambda: state.balances is not None)  # initial load
        assert rig.venue.balance_calls == 1
        rig.venue.push(order("o-1"), fill())
        await until(lambda: rig.venue.balance_calls == 2)
        await until(lambda: state.balances["BTCB2"].available == D("2"))
        assert state.fills == [fill()]
        (line,) = rig.lines()
        assert (line["event"], line["venue"], line["client_id"]) == ("fill", "nk", "t-abc123")
        assert line["data"] == {
            "trade_id": "f-1",
            "order_id": "o-1",
            "side": "buy",
            "qty": "0.004",
            "price": "0.0072",
            "fee": "0.00000006",
            "fee_asset": "BTC",
        }
        assert ("nk", "fill") in rig.seen and ("nk", "balances") in rig.seen


@pytest.mark.spec("RF-05")
async def test_balances_every_few_seconds(tmp_path):
    async with Rig(tmp_path, every=0.05) as rig:
        await until(lambda: rig.venue.balance_calls >= 3, timeout=1.0)


@pytest.mark.spec("RF-05")
async def test_balance_failure_keeps_previous(tmp_path):
    async with Rig(tmp_path, every=0.02) as rig:
        state = rig.account.state["nk"]
        await until(lambda: state.balances is not None)
        before = state.balances
        rig.venue.fail_balances = True
        calls = rig.venue.balance_calls
        await until(lambda: rig.venue.balance_calls > calls + 1)
        assert state.balances == before and "sin red" in (state.error or "")


@pytest.mark.spec("RF-13")
async def test_no_keys_does_not_loop_retries(tmp_path):
    """ReadOnlyError is not transient: noted and not retried."""
    async with Rig(tmp_path, fail_stream=ReadOnlyError("NonKYC: sin keys")) as rig:
        await until(lambda: rig.venue.stream_calls == 1)
        await asyncio.sleep(0.05)  # retry_s is 0.01: it would have retried several times
        assert rig.venue.stream_calls == 1


@pytest.mark.spec("RF-09")
async def test_fill_of_unknown_order_has_no_client_id(tmp_path):
    """Neoxa does not say a fill's order (D-14)."""
    async with Rig(tmp_path) as rig:
        rig.venue.push(Fill("nk", "f-9", None, "sell", D("0.0073"), D("0.01"), D("0"), None, T0))
        await until(lambda: rig.lines())
        line = rig.lines()[0]
        assert line["client_id"] is None and line["data"]["order_id"] is None


@pytest.mark.spec("RF-07")
async def test_forwards_each_event_to_those_asking(tmp_path):
    async with Rig(tmp_path) as rig:
        got = []
        rig.account.subscribe_events(lambda v, ev: got.append((v, ev)))
        rig.venue.push(order("a"), fill())
        await until(lambda: len(got) == 2)
        assert got == [("nk", order("a")), ("nk", fill())]


@pytest.mark.spec("RF-05")
async def test_own_order_also_refreshes_balances(tmp_path):
    """Opening or cancelling moves balance between available and locked."""
    async with Rig(tmp_path) as rig:
        state = rig.account.state["nk"]
        await until(lambda: state.balances is not None)
        rig.venue.push(order("a"))
        await until(lambda: rig.venue.balance_calls == 2)


def errors(rig):
    return [
        (ln["data"]["where"], ln["data"]["error"]) for ln in rig.lines() if ln["event"] == "error"
    ]


@pytest.mark.spec("RF-11", "RF-08")
async def test_private_channel_error_to_journal_once(tmp_path):
    """D-22: NonKYC's "BUY" dropped the stream every few seconds and left no trace."""
    async with Rig(
        tmp_path, fail_stream=VenueError("NonKYC: lado de trade desconocido 'BUY'")
    ) as rig:
        await until(lambda: rig.venue.stream_calls >= 5)
        assert errors(rig) == [("private", "NonKYC: lado de trade desconocido 'BUY'")]
        rig.venue.fail_stream = VenueError("otro")
        await until(lambda: len(errors(rig)) == 2)
        assert errors(rig)[-1] == ("private", "otro")


@pytest.mark.spec("RF-11", "RF-05")
async def test_balance_error_to_journal_once_per_streak(tmp_path):
    async with Rig(tmp_path, every=0.01) as rig:
        rig.venue.fail_balances = True
        start = rig.venue.balance_calls
        await until(lambda: rig.venue.balance_calls >= start + 5)
        assert errors(rig) == [("balances", "sin red")]
        rig.venue.fail_balances = False
        ok = rig.venue.balance_calls
        await until(lambda: rig.venue.balance_calls >= ok + 2)
        rig.venue.fail_balances = True  # a new streak is noted again
        await until(lambda: len(errors(rig)) == 2)


@pytest.mark.spec("RF-11", "RF-08")
async def test_same_error_after_recovering_is_noted_again(tmp_path):
    async with Rig(tmp_path) as rig:
        rig.venue.push(VenueError("caído"))
        await until(lambda: len(errors(rig)) == 1)
        rig.venue.push(VenueError("caído"))  # same streak: not repeated
        await until(lambda: rig.venue.stream_calls >= 3)
        rig.venue.push(order("a"))  # orders arrive: the channel works
        await until(lambda: "a" in rig.account.state["nk"].orders)
        rig.venue.push(VenueError("caído"))
        await until(lambda: len(errors(rig)) == 2)


@pytest.mark.spec("RF-05")
async def test_interval_does_not_add_read_time(tmp_path):
    """CA-01 saw balances 10.4 s old with a 10 s refresh: it waited 10 s after each read
    ended (~0.4 s). Reads must start every 10 s."""
    import time
    from itertools import pairwise

    starts = []

    async def slow_balances():
        starts.append(time.perf_counter())
        await asyncio.sleep(0.06)
        return {"BTC": Balance("BTC", D("1"))}

    async with Rig(tmp_path, every=0.2) as rig:
        rig.venue.balances = slow_balances
        await until(lambda: len(starts) >= 4, timeout=2.0)
    gaps = [b - a for a, b in pairwise(starts)]
    assert all(g < 0.24 for g in gaps[1:]), gaps  # 0.2 interval, not 0.26


@pytest.mark.spec("RF-11", "RF-19")
async def test_textless_error_keeps_its_type(tmp_path):
    """D-51: ConnectTimeout comes with empty text; the account left it as "" and the bot
    took it for "no error"."""
    async with Rig(tmp_path, every=0.01) as rig:
        rig.venue.fail_balances = httpx.ConnectTimeout("")
        await until(lambda: rig.account.state["nk"].error is not None)
        assert rig.account.state["nk"].error == "ConnectTimeout"
        await until(lambda: errors(rig) != [])
        assert errors(rig) == [("balances", "ConnectTimeout")]


@pytest.mark.spec("RF-11", "RF-25")
async def test_corrected_fee_to_journal_without_another_fill(tmp_path):
    async with Rig(tmp_path) as rig:
        state = rig.account.state["nk"]
        await until(lambda: state.balances is not None)
        rig.venue.push(order("o-1"), fill())
        await until(lambda: len(rig.lines()) == 1)
        rig.seen.clear()
        rig.venue.push(FeeUpdate("nk", "f-1", "o-1", D("0.0000893166"), "NKYC", T0 + 9))
        await until(lambda: len(rig.lines()) == 2)
        line = rig.lines()[1]
        assert (line["event"], line["venue"], line["client_id"]) == ("fill_fee", "nk", "t-abc123")
        assert line["data"] == {
            "trade_id": "f-1",
            "order_id": "o-1",
            "fee": "0.0000893166",
            "fee_asset": "NKYC",
        }
        (f,) = state.fills
        assert (f.qty, f.fee, f.fee_asset) == (D("0.004"), D("0.0000893166"), "NKYC")
        assert ("nk", "fill") in rig.seen
