"""Cliente del socket del daemon (T-53, RF-31, D-61): la cuenta y el bot del
daemon con la cara que usa la app, y reconexión si el daemon se va."""

import asyncio

import pytest

from btcb2_terminal.core.models import Balance, D
from btcb2_terminal.services.control import ControlServer
from btcb2_terminal.services.engine import Engine
from btcb2_terminal.services.remote import DaemonGone, RemoteEngine, probe

from .test_control import FakeBot
from .test_uncertain import Rig


class Daemon:
    def __init__(self, tmp_path, bot=True):
        self.s = Rig(tmp_path)
        self.bot = FakeBot() if bot else None
        self.engine = Engine(
            self.s.hub, mode="dry-run", orders=self.s.orders, bot=self.bot, journal=self.s.journal
        )
        self.path = tmp_path / "d.sock"
        self.server = ControlServer(self.engine, self.path, every_s=0.05)

    async def __aenter__(self):
        await self.server.start()
        return self

    async def __aexit__(self, *exc):
        await self.server.aclose()


async def until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.fixture
async def linked(tmp_path):
    async with Daemon(tmp_path) as daemon:
        remote = RemoteEngine(daemon.path, has_bot=True, retry_s=0.02)
        task = asyncio.create_task(remote.run())
        await until(lambda: remote.connected and remote.snapshot is not None)
        yield daemon, remote
        task.cancel()
        await remote.aclose()


@pytest.mark.spec("RF-31")
class TestProbar:
    async def test_dice_el_modo_y_si_hay_bot(self, tmp_path):
        async with Daemon(tmp_path) as d:
            hello = await probe(d.path)
        assert (hello.mode, hello.has_bot) == ("dry-run", True)

    async def test_sin_bot(self, tmp_path):
        async with Daemon(tmp_path, bot=False) as d:
            assert not (await probe(d.path)).has_bot

    async def test_sin_daemon(self, tmp_path):
        with pytest.raises(DaemonGone, match="btcb2t daemon"):
            await probe(tmp_path / "no.sock")

    async def test_una_ruta_que_no_cabe_se_explica(self, tmp_path):
        with pytest.raises(DaemonGone, match="no se puede abrir el socket"):
            await probe(tmp_path / ("x" * 120) / "d.sock")


@pytest.mark.spec("RF-31")
class TestEstado:
    async def test_con_que_venues_opera_el_daemon(self, linked):
        _, remote = linked
        assert remote.mode == "dry-run"
        assert remote.can_trade == {"nk": True, "nx": False}

    async def test_saldos_y_ordenes_de_la_cuenta(self, tmp_path):
        from btcb2_terminal.services.account import AccountService

        from .test_account import FakePrivate, order

        async with Daemon(tmp_path) as d:
            venue = FakePrivate()
            venue.push(order("o-1"))
            account = AccountService({"nk": venue}, retry_s=0.01)
            d.engine.account = account
            runner = asyncio.create_task(account.run())
            remote = RemoteEngine(d.path, has_bot=True, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            heard = []
            remote.account.subscribe(lambda vid, what: heard.append((vid, what)))
            await until(lambda: "nk" in remote.account.state)
            await until(lambda: remote.account.state["nk"].balances is not None)
            st = remote.account.state["nk"]
            assert st.balances["BTC"] == Balance("BTC", D("0.001"))
            assert list(st.orders) == ["o-1"]
            assert ("nk", "balances") in heard and ("nk", "orders") in heard
            task.cancel()
            runner.cancel()
            await remote.aclose()

    async def test_el_bot_y_sus_cambios(self, linked):
        daemon, remote = linked
        states = []
        remote.bot.subscribe(states.append)
        assert remote.bot.state.status == "off"
        assert remote.bot.summary.startswith("BOT") and remote.bot.refusal() is None
        assert remote.bot.status_text() == "bot off"
        assert remote.bot.arm() is None
        await until(lambda: daemon.bot.state.status == "armed")
        await until(lambda: remote.bot.state.status == "armed")
        assert [s.status for s in states] == ["armed"]  # solo al cambiar
        remote.bot.disarm()
        await until(lambda: remote.bot.state.status == "off")
        assert daemon.bot.state.status == "off"

    async def test_inciertas_y_dobles(self, linked):
        daemon, remote = linked
        daemon.s.lose_reply(create=False)
        from .test_uncertain import buy

        await daemon.s.send(buy())
        await until(lambda: len(remote.uncertain) == 1)
        assert remote.uncertain[0].client_id == "t-000001"
        assert remote.doubles == {} and remote.hanging == {} and remote.in_flight == 0


@pytest.mark.spec("RF-31")
class TestAvisos:
    async def test_avisos_y_fallos_del_daemon(self, linked):
        daemon, remote = linked
        notices = []
        remote.subscribe(notices.append)
        daemon.engine._say("⚠ pierna colgada en d-1", error=True)
        daemon.s.orders.faults.report("ui", RuntimeError("bug"))
        await until(lambda: len(notices) >= 2)
        assert notices[0].text == "⚠ pierna colgada en d-1" and notices[0].error
        assert "bug" in notices[1].text and notices[1].error
        await until(lambda: remote.faults == 1)

    async def test_un_rechazo_del_daemon_se_avisa(self, linked):
        daemon, remote = linked
        daemon.bot._refusal = "en live el bot solo se arma con bot.enabled = true"
        notices = []
        remote.subscribe(notices.append)
        remote.bot.arm()
        await until(lambda: notices)
        assert "bot.enabled" in notices[0].text and notices[0].error


@pytest.mark.spec("RF-31")
class TestReconectar:
    async def test_se_va_el_daemon_y_vuelve(self, tmp_path):
        async with Daemon(tmp_path) as d:
            remote = RemoteEngine(d.path, has_bot=True, retry_s=0.02)
            links = []
            remote.subscribe_link(links.append)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.connected)
            await d.server.aclose()
            await until(lambda: not remote.connected)
            remote.bot.arm()  # sin conexión: no peta, avisa
            await d.server.start()
            await until(lambda: remote.connected and links[-1] is True)
            assert links[:3] == [True, False, True]
            task.cancel()
            await remote.aclose()

    async def test_sin_conexion_la_orden_no_sale_y_se_dice(self, tmp_path):
        remote = RemoteEngine(tmp_path / "no.sock", has_bot=True)
        notices = []
        remote.subscribe(notices.append)
        remote.bot.arm()
        assert notices[0].error and "sin conexión con el daemon" in notices[0].text
