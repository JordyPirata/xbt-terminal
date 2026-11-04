"""Isolated faults (T-27, RNF-14, D-28): a bug in a venue or service stops nothing else,
stays in the journal with its redacted traceback and is retried."""

import asyncio
import json

import pytest

from xbt_terminal.core.commands import CancelVenue
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.secrets import Redactor, Secret
from xbt_terminal.services.account import AccountService
from xbt_terminal.services.faults import Faults, supervised
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import Failed, Sent

from ..fakes import T0, FakeFeed
from .test_account import FakePrivate
from .test_orders import Setup, buy

KEY = "s3cr3t-api-key-0123456789"


def lines(logs):
    files = sorted(logs.glob("journal-*.jsonl"))
    return [json.loads(ln) for f in files for ln in f.read_text().splitlines()]


async def until(predicate, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


def full_disk(journal, *events):
    """`journal.write` raises OSError on those events, as with a full disk."""
    real = journal.write

    def write(event, **kw):
        if event in events:
            raise OSError(28, "No space left on device")
        return real(event, **kw)

    journal.write = write


@pytest.mark.spec("RNF-14")
class TestFaults:
    def test_to_journal_with_redacted_traceback(self, tmp_path):
        journal = Journal(tmp_path, "live", Redactor([Secret(KEY)]))
        faults = Faults(journal, clock=lambda: T0)
        try:
            raise RuntimeError(f"bug con la key {KEY} dentro")
        except RuntimeError as exc:
            fault = faults.report("market", exc, venue="nk")
        (line,) = lines(tmp_path)
        assert (line["event"], line["venue"]) == ("error", "nk")
        assert line["data"]["where"] == "market"
        assert line["data"]["error"] == "RuntimeError: bug con la key *** dentro"
        assert "Traceback" in line["data"]["traceback"]
        assert "test_to_journal_with_redacted_traceback" in line["data"]["traceback"]
        assert KEY not in json.dumps(line)
        assert faults.recent == [fault] and fault.ts == T0
        assert KEY not in fault.message  # nor what shows on screen

    def test_without_working_journal_notifies_anyway(self, tmp_path):
        journal = Journal(tmp_path, "live", Redactor([]))
        full_disk(journal, "error")
        faults = Faults(journal)
        seen = []
        faults.subscribe(seen.append)
        fault = faults.report("market", RuntimeError("bug"), venue="nk")
        assert seen == [fault] and "RuntimeError: bug" in fault.message

    async def test_supervised_retries_with_backoff(self, monkeypatch):
        crashes, calls, waits = [], [], []
        real_sleep = asyncio.sleep

        async def sleep(s):
            waits.append(s)
            await real_sleep(0)

        monkeypatch.setattr("xbt_terminal.services.faults.asyncio.sleep", sleep)

        async def work():
            calls.append(1)
            if len(calls) < 6:
                raise RuntimeError(f"bug {len(calls)}")

        await asyncio.wait_for(supervised(work, crashes.append, retry_s=1.0, max_s=8.0), 1)
        assert [str(e) for e in crashes] == [f"bug {i}" for i in range(1, 6)]
        assert waits == [1.0, 2.0, 4.0, 8.0, 8.0]

    async def test_supervised_does_not_swallow_cancellation(self):
        task = asyncio.create_task(supervised(asyncio.Event().wait, print, retry_s=0.01))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class Buggy(FakeFeed):
    """`market()` has a bug the first `n` times (not a network error)."""

    def __init__(self, venue, n=1):
        super().__init__(venue)
        self.bugs = n

    async def market(self):
        if self.bugs:
            self.bugs -= 1
            raise KeyError("tickSize")
        return await super().market()


@pytest.mark.spec("RNF-14", "RF-14")
class TestHub:
    async def test_venue_bug_does_not_stop_other_and_retries(self, tmp_path):
        journal = Journal(tmp_path, "live", Redactor([]))
        faults = Faults(journal)
        feeds = {"nk": Buggy("nk"), "nx": FakeFeed("nx")}
        hub = MarketHub(feeds, history=5, retry_s=0.01, faults=faults)
        task = asyncio.create_task(hub.run())
        try:
            await until(lambda: hub.state["nx"].ready and hub.state["nk"].ready)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        (fault,) = faults.recent
        assert (fault.where, fault.venue) == ("market", "nk") and "KeyError" in fault.message
        assert [ln["data"]["where"] for ln in lines(tmp_path)] == ["market"]


@pytest.mark.spec("RNF-14", "RF-05")
class TestAccount:
    async def test_balance_bug_does_not_stop_private_channel(self, tmp_path):
        venue = FakePrivate()
        real = venue.balances
        bugs = [ZeroDivisionError("división")]

        async def balances():
            if bugs:
                raise bugs.pop()
            return await real()

        venue.balances = balances
        journal = Journal(tmp_path, "live", Redactor([]))
        faults = Faults(journal)
        account = AccountService(
            {"nk": venue}, journal=journal, retry_s=0.01, clock=lambda: T0, faults=faults
        )
        task = asyncio.create_task(account.run())
        try:
            await until(lambda: account.state["nk"].balances is not None)
            assert venue.stream_calls == 1  # the stream stayed open
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        (fault,) = faults.recent
        assert (fault.where, fault.venue) == ("balances", "nk")


@pytest.mark.spec("RNF-14", "RF-11")
class TestJournalAfterSending:
    async def test_order_ack_without_disk_order_still_counts(self, tmp_path):
        s = Setup(tmp_path)
        full_disk(s.journal, "order_ack")
        seen = []
        s.orders.faults.subscribe(seen.append)
        result = await s.orders.confirm(await s.orders.prepare(buy(price="0.00725000")))
        assert isinstance(result, Sent)
        (fault,) = seen
        assert fault.where == "journal" and "dry-nk-1 enviada" in fault.message
        assert "No space left" in fault.message
        again = await s.orders.prepare(buy(price="0.00725000"))
        assert again is not None  # the service keeps working

    async def test_no_order_sent_nothing_sent(self, tmp_path):
        """Without a trace of what is about to be sent, it is not sent (RF-11)."""
        s = Setup(tmp_path)
        full_disk(s.journal, "order_sent")
        result = await s.orders.confirm(await s.orders.prepare(buy()))
        assert isinstance(result, Failed) and "journal" in result.error
        assert await s.venue.open_orders() == []

    async def test_cancels_even_if_journal_fails(self, tmp_path):
        """Cancelling reduces risk: it is not skipped because of the disk."""
        s = Setup(tmp_path)
        await s.orders.confirm(await s.orders.prepare(buy()))
        full_disk(s.journal, "cancel_sent", "cancel_ack")
        plan = await s.orders.plan_cancel(CancelVenue("nk"))
        result = await s.orders.execute_cancel(plan)
        assert result.ok and await s.venue.open_orders() == []
        assert {f.where for f in s.orders.faults.recent} == {"journal"}
