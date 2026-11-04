"""Daemon without a screen (T-51, RF-30, D-61): runs the engine until asked to stop,
and on stopping disarms the bot and leaves sends in flight in the journal."""

import asyncio

import pytest

from xbt_terminal.services.account import AccountService
from xbt_terminal.services.daemon import run_daemon
from xbt_terminal.services.engine import Engine

from .test_account import FakePrivate, order
from .test_orders import NOW
from .test_uncertain import Rig, buy


class FakeBot:
    """What the daemon uses of the bot: running and disarming."""

    def __init__(self):
        self.ran = asyncio.Event()
        self.disarmed = False

    async def run(self):
        self.ran.set()
        await asyncio.Event().wait()

    def disarm(self):
        self.disarmed = True


def engine_for(s, *, mode="dry-run", **kw):
    return Engine(s.hub, mode=mode, orders=s.orders, journal=s.journal, **kw)


async def until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.spec("RF-30")
class TestRun:
    async def test_starts_notifies_and_stops_when_asked(self, tmp_path):
        s = Rig(tmp_path)
        bot = FakeBot()
        engine = engine_for(s, bot=bot)
        stop, ready = asyncio.Event(), []
        daemon = asyncio.create_task(run_daemon(engine, stop, on_ready=lambda: ready.append(1)))
        await asyncio.wait_for(bot.ran.wait(), 1)
        assert ready == [1] and not daemon.done()
        stop.set()
        await asyncio.wait_for(daemon, 1)
        assert bot.disarmed

    async def test_error_notices_go_to_journal(self, tmp_path):
        s = Rig(tmp_path)
        s.hub._venues["nk"].clock_skew_ms = 9_000
        engine = engine_for(s, max_clock_skew_s=5)
        stop = asyncio.Event()
        daemon = asyncio.create_task(run_daemon(engine, stop))
        await asyncio.sleep(0)
        engine.check_skew("nk")
        engine._say("todo bien")  # non-errors only go to the log
        stop.set()
        await daemon
        (line,) = [ln for ln in s.lines() if ln["event"] == "notice"]
        assert "reloj desfasado" in line["data"]["text"]


@pytest.mark.spec("RF-30", "RNF-15")
class TestStopping:
    async def test_send_in_flight_reaches_journal(self, tmp_path):
        s = Rig(tmp_path)
        gate = asyncio.Event()
        real = s.venue.place_limit

        async def slow(req):
            await gate.wait()
            return await real(req)

        s.venue.place_limit = slow
        stop = asyncio.Event()
        daemon = asyncio.create_task(run_daemon(engine_for(s), stop))
        sending = asyncio.create_task(s.send(buy()))
        await until(lambda: s.orders.in_flight)
        stop.set()
        await asyncio.sleep(0.01)
        assert not daemon.done()  # waits for the send
        gate.set()
        await asyncio.wait_for(daemon, 1)
        await sending
        assert s.events() == ["order_sent", "order_ack"]

    async def test_live_with_open_orders_notes_it(self, tmp_path):
        s = Rig(tmp_path)
        venue = FakePrivate()
        venue.push(order("o-1"), order("o-2"))
        account = AccountService({"nk": venue}, retry_s=0.01, clock=lambda: NOW)
        engine = engine_for(s, mode="live", account=account)
        stop = asyncio.Event()
        daemon = asyncio.create_task(run_daemon(engine, stop))
        await until(lambda: len(account.state["nk"].orders) == 2)
        stop.set()
        await daemon
        (line,) = [ln for ln in s.lines() if ln["event"] == "notice"]
        assert "2 órdenes abiertas" in line["data"]["text"]

    async def test_dry_run_does_not_warn_of_open_orders(self, tmp_path):
        s = Rig(tmp_path)
        venue = FakePrivate()
        venue.push(order("o-1"))
        account = AccountService({"nk": venue}, retry_s=0.01, clock=lambda: NOW)
        engine = engine_for(s, account=account)
        stop = asyncio.Event()
        daemon = asyncio.create_task(run_daemon(engine, stop))
        await until(lambda: len(account.state["nk"].orders) == 1)
        stop.set()
        await daemon
        assert "notice" not in s.events()


@pytest.mark.spec("RF-30", "RF-31")
async def test_opens_socket_and_closes_on_stop(tmp_path):
    s = Rig(tmp_path)
    path = tmp_path / "xbt.sock"
    stop = asyncio.Event()
    daemon = asyncio.create_task(run_daemon(engine_for(s), stop, socket=path))
    await until(path.exists)
    reader, writer = await asyncio.open_unix_connection(str(path))
    assert b'"hello"' in await asyncio.wait_for(reader.readline(), 1)
    stop.set()
    await asyncio.wait_for(daemon, 1)
    assert await asyncio.wait_for(reader.read(), 1) is not None  # closed by the daemon
    assert not path.exists()
    writer.close()
