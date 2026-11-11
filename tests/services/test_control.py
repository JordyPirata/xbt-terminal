"""Daemon control socket (T-52, RF-31, D-61): snapshot on connecting, state on change,
notices, and only `bot_on`, `bot_off` and `ping` as commands."""

import asyncio
import json
import stat

import pytest

from xbt_terminal.core.bot import BotState
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.secrets import Redactor, Secret
from xbt_terminal.services.control import ControlServer
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.faults import Faults
from xbt_terminal.services.wire import Snapshot, decode

from .test_uncertain import Rig


class FakeBot:
    """The bot face used by the engine and the socket."""

    summary = "BOT DE ARBITRAJE TAKER"

    def __init__(self, refusal=None):
        self.state = BotState()
        self._refusal = refusal
        self._listeners = []

    def subscribe(self, listener):
        self._listeners.append(listener)

    def refusal(self):
        return self._refusal

    def status_text(self):
        return f"bot {self.state.status}"

    def arm(self):
        if self._refusal:
            return self._refusal
        self._set(BotState("armed"))
        return None

    def disarm(self):
        self._set(BotState("off"))

    def _set(self, state):
        self.state = state
        for listener in self._listeners:
            listener(state)


class Client:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, path):
        return cls(*await asyncio.open_unix_connection(str(path)))

    async def recv(self, timeout=1.0):
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        assert line, "el daemon cerró la conexión"
        return json.loads(line)

    async def until(self, kind, timeout=1.0):
        async with asyncio.timeout(timeout):
            while True:
                msg = await self.recv()
                if msg["type"] == kind:
                    return msg

    async def send(self, raw):
        self.writer.write(raw if isinstance(raw, bytes) else json.dumps(raw).encode() + b"\n")
        await self.writer.drain()

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


@pytest.fixture
async def rig(tmp_path):
    rig_obj = Rig(tmp_path)
    rig_obj.bot = FakeBot()
    rig_obj.engine = Engine(
        rig_obj.hub, mode="dry-run", orders=rig_obj.orders, bot=rig_obj.bot, journal=rig_obj.journal
    )
    rig_obj.path = tmp_path / "xbt.sock"
    rig_obj.server = ControlServer(rig_obj.engine, rig_obj.path, every_s=0.05)
    await rig_obj.server.start()
    yield rig_obj
    await rig_obj.server.aclose()


@pytest.mark.spec("RF-31")
class TestConnect:
    async def test_socket_user_only(self, rig):
        assert stat.S_IMODE(rig.path.stat().st_mode) == 0o600

    async def test_hello_and_snapshot(self, rig):
        client = await Client.open(rig.path)
        hello = await client.recv()
        assert hello["type"] == "hello" and hello["mode"] == "dry-run" and hello["version"]
        state = await client.recv()
        assert state["type"] == "state"
        snap = decode(Snapshot, state["state"])
        assert snap.bot.state.status == "off" and snap.bot.summary.startswith("BOT")
        assert snap.can_trade == {"nk": True, "nx": False}
        await client.close()

    async def test_several_connections_at_once(self, rig):
        first_client, second_client = await Client.open(rig.path), await Client.open(rig.path)
        assert (
            (await first_client.recv())["type"] == (await second_client.recv())["type"] == "hello"
        )
        await first_client.close()
        await second_client.close()

    async def test_closing_removes_socket(self, tmp_path, rig):
        await rig.server.aclose()
        assert not rig.path.exists()

    async def test_old_socket_replaced(self, tmp_path, rig):
        """The daemon holds the lock: a leftover socket belongs to a dead one."""
        await rig.server.aclose()
        rig.path.write_text("viejo")
        await rig.server.start()
        client = await Client.open(rig.path)
        assert (await client.recv())["type"] == "hello"
        await client.close()


@pytest.mark.spec("RF-31", "RF-18")
class TestOrders:
    async def test_bot_on_and_off(self, rig):
        client = await Client.open(rig.path)
        await client.send({"cmd": "bot_on"})
        reply = await client.until("reply")
        assert reply == {"type": "reply", "cmd": "bot_on", "error": None}
        assert rig.bot.state.status == "armed"
        state = await client.until("state")
        while decode(Snapshot, state["state"]).bot.state.status != "armed":
            state = await client.until("state")
        await client.send({"cmd": "bot_off"})
        assert (await client.until("reply"))["cmd"] == "bot_off"
        assert rig.bot.state.status == "off"
        await client.close()

    async def test_refused_bot_on_says_why(self, rig):
        rig.bot._refusal = "en live el bot solo se arma con bot.enabled = true"
        client = await Client.open(rig.path)
        await client.send({"cmd": "bot_on"})
        assert "bot.enabled" in (await client.until("reply"))["error"]
        assert rig.bot.state.status == "off"
        await client.close()

    @pytest.mark.parametrize(
        "raw",
        [
            {"cmd": "buy", "args": "nk 0.01 0.007"},
            {"cmd": "cancel_all"},
            {"nada": 1},
            b"no es json\n",
        ],
    )
    async def test_rest_rejected_and_connection_stays(self, rig, raw):
        client = await Client.open(rig.path)
        await client.send(raw)
        assert (await client.until("error"))["error"]
        await client.send({"cmd": "ping"})
        assert (await client.until("pong"))["type"] == "pong"
        assert rig.journal_dir.glob("journal-*.jsonl")  # nothing sent to the venue
        assert rig.events() == []
        await client.close()


@pytest.mark.spec("RF-31")
class TestEvents:
    async def test_notices_and_faults(self, rig):
        client = await Client.open(rig.path)
        await client.until("state")
        rig.engine._say("⚠ pierna colgada en d-1", error=True)
        notice = await client.until("notice")
        assert notice == {"type": "notice", "text": "⚠ pierna colgada en d-1", "error": True}
        rig.orders.faults.report("ui", RuntimeError("bug"))
        assert "bug" in (await client.until("fault"))["message"]
        await client.close()

    async def test_state_repeats_even_if_unchanged(self, rig):
        """The bar shows ages: the snapshot arrives at least every `every_s`."""
        client = await Client.open(rig.path)
        await client.until("state")
        await client.until("state", timeout=0.5)
        await client.close()

    async def test_non_reading_client_disconnected(self, tmp_path):
        """Its buffer fills, so does its queue, and it gets closed; the other goes on."""
        rig = Rig(tmp_path)
        engine = Engine(rig.hub, mode="dry-run", orders=rig.orders, journal=rig.journal)
        path = tmp_path / "c.sock"
        server = ControlServer(engine, path, every_s=60, queue_max=3)
        await server.start()
        slow, ok = await Client.open(path), await Client.open(path)
        await ok.until("state")
        seen = []

        async def read_all():
            while True:
                msg = await ok.recv(timeout=5)
                if msg["type"] == "notice":
                    seen.append(msg["text"][:7])

        reading = asyncio.create_task(read_all())
        for i in range(40):
            engine._say(f"aviso{i:02d}" + "x" * 64_000)
            await asyncio.sleep(0.001)
        async with asyncio.timeout(2):
            while server.clients > 1 or len(seen) < 40:
                await asyncio.sleep(0.01)
        reading.cancel()
        assert seen[0] == "aviso00" and seen[-1] == "aviso39"
        await slow.close()
        await ok.close()
        await server.aclose()


@pytest.mark.spec("RF-31", "CA-07")
async def test_no_secret_leaves_by_socket(tmp_path):
    canary = Secret("k_CANARIO_socket")
    rig = Rig(tmp_path)
    journal = Journal(tmp_path / "j", "dry-run", Redactor([canary]))
    faults = Faults(journal)
    rig.orders.faults = faults
    engine = Engine(rig.hub, mode="dry-run", orders=rig.orders, journal=journal)
    path = tmp_path / "c.sock"
    server = ControlServer(engine, path, every_s=0.05)
    await server.start()
    client = await Client.open(path)
    await client.until("state")  # already registered
    faults.report("private", RuntimeError(f"401 con key {canary.reveal()}"))
    engine._say(f"aviso con {canary.reveal()}", error=True)
    raw = b""
    async with asyncio.timeout(1):
        while b"notice" not in raw:
            raw += await client.reader.readline()
    assert b"CANARIO" not in raw and b"***" in raw
    await client.close()
    await server.aclose()
