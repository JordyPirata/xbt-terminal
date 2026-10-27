"""Socket de control del daemon (T-52, RF-31, D-61): foto al conectar, estado
al cambiar, avisos, y solo `bot_on`, `bot_off` y `ping` como órdenes."""

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
    """La cara del bot que usan el motor y el socket."""

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
    s = Rig(tmp_path)
    s.bot = FakeBot()
    s.engine = Engine(s.hub, mode="dry-run", orders=s.orders, bot=s.bot, journal=s.journal)
    s.path = tmp_path / "xbt.sock"
    s.server = ControlServer(s.engine, s.path, every_s=0.05)
    await s.server.start()
    yield s
    await s.server.aclose()


@pytest.mark.spec("RF-31")
class TestConectar:
    async def test_socket_solo_para_el_usuario(self, rig):
        assert stat.S_IMODE(rig.path.stat().st_mode) == 0o600

    async def test_hola_y_foto(self, rig):
        c = await Client.open(rig.path)
        hello = await c.recv()
        assert hello["type"] == "hello" and hello["mode"] == "dry-run" and hello["version"]
        state = await c.recv()
        assert state["type"] == "state"
        snap = decode(Snapshot, state["state"])
        assert snap.bot.state.status == "off" and snap.bot.summary.startswith("BOT")
        assert snap.can_trade == {"nk": True, "nx": False}
        await c.close()

    async def test_varias_conexiones_a_la_vez(self, rig):
        a, b = await Client.open(rig.path), await Client.open(rig.path)
        assert (await a.recv())["type"] == (await b.recv())["type"] == "hello"
        await a.close()
        await b.close()

    async def test_al_cerrar_borra_el_socket(self, tmp_path, rig):
        await rig.server.aclose()
        assert not rig.path.exists()

    async def test_un_socket_viejo_se_reemplaza(self, tmp_path, rig):
        """El daemon tiene el lock: un socket que quede es de uno que murió."""
        await rig.server.aclose()
        rig.path.write_text("viejo")
        await rig.server.start()
        c = await Client.open(rig.path)
        assert (await c.recv())["type"] == "hello"
        await c.close()


@pytest.mark.spec("RF-31", "RF-18")
class TestOrdenes:
    async def test_bot_on_y_off(self, rig):
        c = await Client.open(rig.path)
        await c.send({"cmd": "bot_on"})
        reply = await c.until("reply")
        assert reply == {"type": "reply", "cmd": "bot_on", "error": None}
        assert rig.bot.state.status == "armed"
        state = await c.until("state")
        while decode(Snapshot, state["state"]).bot.state.status != "armed":
            state = await c.until("state")
        await c.send({"cmd": "bot_off"})
        assert (await c.until("reply"))["cmd"] == "bot_off"
        assert rig.bot.state.status == "off"
        await c.close()

    async def test_bot_on_rechazado_dice_por_que(self, rig):
        rig.bot._refusal = "en live el bot solo se arma con bot.enabled = true"
        c = await Client.open(rig.path)
        await c.send({"cmd": "bot_on"})
        assert "bot.enabled" in (await c.until("reply"))["error"]
        assert rig.bot.state.status == "off"
        await c.close()

    @pytest.mark.parametrize(
        "raw",
        [
            {"cmd": "buy", "args": "nk 0.01 0.007"},
            {"cmd": "cancel_all"},
            {"nada": 1},
            b"no es json\n",
        ],
    )
    async def test_lo_demas_se_rechaza_y_la_conexion_sigue(self, rig, raw):
        c = await Client.open(rig.path)
        await c.send(raw)
        assert (await c.until("error"))["error"]
        await c.send({"cmd": "ping"})
        assert (await c.until("pong"))["type"] == "pong"
        assert rig.journal_dir.glob("journal-*.jsonl")  # nada enviado al venue
        assert rig.events() == []
        await c.close()


@pytest.mark.spec("RF-31")
class TestEventos:
    async def test_avisos_y_fallos(self, rig):
        c = await Client.open(rig.path)
        await c.until("state")
        rig.engine._say("⚠ pierna colgada en d-1", error=True)
        notice = await c.until("notice")
        assert notice == {"type": "notice", "text": "⚠ pierna colgada en d-1", "error": True}
        rig.orders.faults.report("ui", RuntimeError("bug"))
        assert "bug" in (await c.until("fault"))["message"]
        await c.close()

    async def test_el_estado_se_repite_aunque_no_cambie(self, rig):
        """La barra muestra edades: la foto llega cada `every_s` como mínimo."""
        c = await Client.open(rig.path)
        await c.until("state")
        await c.until("state", timeout=0.5)
        await c.close()

    async def test_un_cliente_que_no_lee_se_desconecta(self, tmp_path):
        """Su buffer se llena, su cola también, y se le cierra; el otro sigue."""
        s = Rig(tmp_path)
        engine = Engine(s.hub, mode="dry-run", orders=s.orders, journal=s.journal)
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
async def test_ningun_secreto_sale_por_el_socket(tmp_path):
    canary = Secret("k_CANARIO_socket")
    s = Rig(tmp_path)
    journal = Journal(tmp_path / "j", "dry-run", Redactor([canary]))
    faults = Faults(journal)
    s.orders.faults = faults
    engine = Engine(s.hub, mode="dry-run", orders=s.orders, journal=journal)
    path = tmp_path / "c.sock"
    server = ControlServer(engine, path, every_s=0.05)
    await server.start()
    c = await Client.open(path)
    await c.until("state")  # ya está dado de alta
    faults.report("private", RuntimeError(f"401 con key {canary.reveal()}"))
    engine._say(f"aviso con {canary.reveal()}", error=True)
    raw = b""
    async with asyncio.timeout(1):
        while b"notice" not in raw:
            raw += await c.reader.readline()
    assert b"CANARIO" not in raw and b"***" in raw
    await c.close()
    await server.aclose()
