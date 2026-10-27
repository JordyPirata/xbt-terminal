"""Daemon sin pantalla (T-51, RF-30, D-61): corre el motor hasta que le piden
parar, y al parar desarma el bot y deja los envíos en vuelo en el journal."""

import asyncio

import pytest

from xbt_terminal.services.account import AccountService
from xbt_terminal.services.daemon import run_daemon
from xbt_terminal.services.engine import Engine

from .test_account import FakePrivate, order
from .test_orders import NOW
from .test_uncertain import Rig, buy


class FakeBot:
    """Lo que el daemon usa del bot: correr y desarmarse."""

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
class TestCorrer:
    async def test_arranca_avisa_y_para_al_pedirlo(self, tmp_path):
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

    async def test_los_avisos_de_error_van_al_journal(self, tmp_path):
        s = Rig(tmp_path)
        s.hub._venues["nk"].clock_skew_ms = 9_000
        engine = engine_for(s, max_clock_skew_s=5)
        stop = asyncio.Event()
        daemon = asyncio.create_task(run_daemon(engine, stop))
        await asyncio.sleep(0)
        engine.check_skew("nk")
        engine._say("todo bien")  # los que no son error solo van al log
        stop.set()
        await daemon
        (line,) = [ln for ln in s.lines() if ln["event"] == "notice"]
        assert "reloj desfasado" in line["data"]["text"]


@pytest.mark.spec("RF-30", "RNF-15")
class TestParar:
    async def test_el_envio_en_vuelo_llega_al_journal(self, tmp_path):
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
        assert not daemon.done()  # espera al envío
        gate.set()
        await asyncio.wait_for(daemon, 1)
        await sending
        assert s.events() == ["order_sent", "order_ack"]

    async def test_en_live_con_ordenes_abiertas_lo_apunta(self, tmp_path):
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

    async def test_en_dry_run_no_avisa_de_abiertas(self, tmp_path):
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
async def test_abre_el_socket_y_lo_cierra_al_parar(tmp_path):
    s = Rig(tmp_path)
    path = tmp_path / "xbt.sock"
    stop = asyncio.Event()
    daemon = asyncio.create_task(run_daemon(engine_for(s), stop, socket=path))
    await until(path.exists)
    reader, writer = await asyncio.open_unix_connection(str(path))
    assert b'"hello"' in await asyncio.wait_for(reader.readline(), 1)
    stop.set()
    await asyncio.wait_for(daemon, 1)
    assert await asyncio.wait_for(reader.read(), 1) is not None  # cerrado por el daemon
    assert not path.exists()
    writer.close()
