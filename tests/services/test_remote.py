"""Daemon socket client (T-53, RF-31, D-61): the daemon's account and bot with the face
the app uses, and reconnection if the daemon leaves."""

import asyncio

import pytest

from xbt_terminal.core.models import Balance, D
from xbt_terminal.services.control import ControlServer
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.remote import DaemonGoneError, RemoteEngine, probe

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

    async def __aexit__(self, *error):
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
class TestProbe:
    async def test_says_mode_and_whether_bot_exists(self, tmp_path):
        async with Daemon(tmp_path) as d:
            hello = await probe(d.path)
        assert (hello.mode, hello.has_bot) == ("dry-run", True)

    async def test_without_bot(self, tmp_path):
        async with Daemon(tmp_path, bot=False) as d:
            assert not (await probe(d.path)).has_bot

    async def test_no_daemon(self, tmp_path):
        with pytest.raises(DaemonGoneError, match="xbt daemon"):
            await probe(tmp_path / "no.sock")

    async def test_unfit_path_is_explained(self, tmp_path):
        with pytest.raises(DaemonGoneError, match="no se puede abrir el socket"):
            await probe(tmp_path / ("x" * 120) / "d.sock")


@pytest.mark.spec("RF-31")
class TestState:
    async def test_which_venues_the_daemon_trades(self, linked):
        _, remote = linked
        assert remote.mode == "dry-run"
        assert remote.can_trade == {"nk": True, "nx": False}

    async def test_account_balances_and_orders(self, tmp_path):
        from xbt_terminal.services.account import AccountService

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
            remote.account.subscribe(lambda venue_id, what: heard.append((venue_id, what)))
            await until(lambda: "nk" in remote.account.state)
            await until(lambda: remote.account.state["nk"].balances is not None)
            state = remote.account.state["nk"]
            assert state.balances["BTC"] == Balance("BTC", D("0.001"))
            assert list(state.orders) == ["o-1"]
            assert ("nk", "balances") in heard and ("nk", "orders") in heard
            task.cancel()
            runner.cancel()
            await remote.aclose()

    async def test_bot_and_its_changes(self, linked):
        daemon, remote = linked
        states = []
        remote.bot.subscribe(states.append)
        assert remote.bot.state.status == "off"
        assert remote.bot.summary.startswith("BOT") and remote.bot.refusal() is None
        assert remote.bot.status_text() == "bot off"
        assert remote.bot.arm() is None
        await until(lambda: daemon.bot.state.status == "armed")
        await until(lambda: remote.bot.state.status == "armed")
        assert [s.status for s in states] == ["armed"]  # only on change
        remote.bot.disarm()
        await until(lambda: remote.bot.state.status == "off")
        assert daemon.bot.state.status == "off"

    async def test_uncertain_and_doubles(self, linked):
        daemon, remote = linked
        daemon.s.lose_reply(create=False)
        from .test_uncertain import buy

        await daemon.s.send(buy())
        await until(lambda: len(remote.uncertain) == 1)
        assert remote.uncertain[0].client_id == "t-000001"
        assert remote.doubles == {} and remote.hanging == {} and remote.in_flight == 0


@pytest.mark.spec("RF-31")
class TestNotices:
    async def test_daemon_notices_and_faults(self, linked):
        daemon, remote = linked
        notices = []
        remote.subscribe(notices.append)
        daemon.engine._say("⚠ pierna colgada en d-1", error=True)
        daemon.s.orders.faults.report("ui", RuntimeError("bug"))
        await until(lambda: len(notices) >= 2)
        assert notices[0].text == "⚠ pierna colgada en d-1" and notices[0].error
        assert "bug" in notices[1].text and notices[1].error
        await until(lambda: remote.faults == 1)

    async def test_daemon_rejection_is_notified(self, linked):
        daemon, remote = linked
        daemon.bot._refusal = "en live el bot solo se arma con bot.enabled = true"
        notices = []
        remote.subscribe(notices.append)
        remote.bot.arm()
        await until(lambda: notices)
        assert "bot.enabled" in notices[0].text and notices[0].error


@pytest.mark.spec("RF-31")
class TestReconnect:
    async def test_daemon_leaves_and_returns(self, tmp_path):
        async with Daemon(tmp_path) as d:
            remote = RemoteEngine(d.path, has_bot=True, retry_s=0.02)
            links = []
            remote.subscribe_link(links.append)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.connected)
            await d.server.aclose()
            await until(lambda: not remote.connected)
            remote.bot.arm()  # no connection: does not crash, warns
            await d.server.start()
            await until(lambda: remote.connected and links[-1] is True)
            assert links[:3] == [True, False, True]
            task.cancel()
            await remote.aclose()

    async def test_unconnected_order_does_not_leave_and_says_so(self, tmp_path):
        remote = RemoteEngine(tmp_path / "no.sock", has_bot=True)
        notices = []
        remote.subscribe(notices.append)
        remote.bot.arm()
        assert notices[0].error and "sin conexión con el daemon" in notices[0].text


@pytest.mark.spec("RF-31")
class TestDaemonMessages:
    """`_handle` dispatches each message type (T-85); no connection, only notices."""

    def heard(self, tmp_path):
        remote = RemoteEngine(tmp_path / "x.sock", has_bot=False)
        out = []
        remote.subscribe(out.append)
        return remote, out

    def test_unknown_type_does_nothing(self, tmp_path):
        remote, out = self.heard(tmp_path)
        remote._handle({"type": "nuevo", "x": 1})
        remote._handle({"x": 1})
        assert out == []

    def test_unawaited_error_reply_is_notified(self, tmp_path):
        remote, out = self.heard(tmp_path)
        remote._handle({"type": "reply", "id": 99, "error": "no"})
        remote._handle({"type": "reply", "id": 98})
        assert [(n.text, n.error) for n in out] == [("no", True)]

    def test_unawaited_error_is_notified(self, tmp_path):
        remote, out = self.heard(tmp_path)
        remote._handle({"type": "error", "id": 97, "error": "sin saldo"})
        assert [(n.text, n.error) for n in out] == [
            ("el daemon no aceptó la orden: sin saldo", True)
        ]

    def test_notice_and_fault(self, tmp_path):
        remote, out = self.heard(tmp_path)
        remote._handle({"type": "notice", "text": "hola", "error": False})
        remote._handle({"type": "fault", "message": "bug"})
        assert [(n.text, n.error) for n in out] == [("hola", False), ("daemon: bug", True)]
