"""CA-15 automático (T-55, RF-30, RF-31, D-61): un daemon en dry-run con
venues simulados arranca con el bot `off`; una TUI conectada lo arma y se
cierra; el bot sigue armado y dispara; otra conexión ve lo mismo; una orden
desde la TUI conectada se rechaza; y la parada deja todo en el journal."""

import asyncio
from dataclasses import replace

import pytest

from xbt_terminal.core.models import D
from xbt_terminal.services.daemon import run_daemon
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.remote import RemoteEngine, probe
from xbt_terminal.ui.app import ConfirmScreen, XbtApp
from xbt_terminal.ui.status import StatusBar

from ..fakes import FakeFeed
from ..services.test_bot_service import BOT, BotRig
from .test_orders_ui import type_command, until


class DaemonRig(BotRig):
    """Las piezas de BotRig, pero las corre `run_daemon`, como en el server, y
    sin enlazar a mano la cuenta con las piernas: eso lo hace el Engine."""

    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, wire=False, **kw)

    async def __aenter__(self):
        self.socket = self.logs.parent / "d.sock"
        self.stop = asyncio.Event()
        self.engine = Engine(
            self.hub, mode="dry-run", orders=self.orders, account=self.account,
            bot=self.bot, journal=self.journal,
        )  # fmt: skip
        self.daemon = asyncio.create_task(run_daemon(self.engine, self.stop, socket=self.socket))
        await self.until(self.socket.exists)
        await self.until(lambda: all(s.ready for s in self.hub.state.values()))
        await self.until(lambda: all(s.balances for s in self.account.state.values()))
        return self

    async def __aexit__(self, *exc):
        self.stop.set()
        await asyncio.wait_for(self.daemon, 2)


def attached_app(rig, has_bot=True):
    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    hub = MarketHub(feeds, history=50, retry_s=0.01, clock=rig.clock)
    remote = RemoteEngine(rig.socket, has_bot=has_bot, retry_s=0.02)
    return XbtApp(
        hub,
        book_levels=15,
        timeframe="1m",
        mode="dry-run",
        account=remote.account,
        bot=remote.bot,
        remote=remote,
        engine=Engine(hub, mode="dry-run"),
        clock=rig.clock,
    )


def bar(app):
    return app.query_one(StatusBar).text


@pytest.mark.spec("CA-15", "RF-30", "RF-31", "RF-18")
async def test_el_bot_sigue_armado_y_dispara_tras_cerrar_la_tui(tmp_path):
    async with DaemonRig(tmp_path) as rig:
        hello = await probe(rig.socket)
        assert (hello.mode, hello.has_bot) == ("dry-run", True)
        assert rig.bot.state.status == "off"  # RF-18: arranca apagado

        app = attached_app(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            await until(pilot, lambda: "⇄ daemon" in bar(app) and app.remote.snapshot)
            # bot on con la confirmación de siempre
            await type_command(pilot, app, "bot on")
            await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
            assert "BOT DE ARBITRAJE TAKER" in app.screen.summary
            await pilot.press("y")
            await until(pilot, lambda: rig.bot.state.status == "armed")
            await until(pilot, lambda: "BOT armado" in bar(app))
            await pilot.press("escape", "q")  # suelta la línea de comandos y sale
            await until(pilot, lambda: not app.is_running)

        # la TUI se fue: el daemon sigue igual
        await asyncio.sleep(0.1)
        assert rig.bot.state.status == "armed"
        assert [e["data"]["status"] for e in rig.events("bot_state")] == ["armed"]
        assert rig.events("order_sent") == []  # nada enviado antes del cruce

        # y dispara cuando el mercado cruza
        rig.books()
        await rig.until(lambda: rig.events("bot_fire"))
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        (double,) = rig.orders.doubles.values()

        # otra conexión ve lo mismo que el daemon
        other = RemoteEngine(rig.socket, has_bot=True, retry_s=0.02)
        task = asyncio.create_task(other.run())
        await rig.until(lambda: other.snapshot is not None and double.id in other.doubles)
        await rig.until(lambda: other.bot.state.status == "cooldown")
        assert other.bot.state.fired_at == rig.bot.state.fired_at
        task.cancel()
        await other.aclose()

    # la parada (lo que hace SIGTERM): bot desarmado, journal y socket cerrados
    assert rig.daemon.done() and rig.daemon.exception() is None
    assert rig.bot.state.status == "off"
    assert rig.events("bot_state")[-1]["data"]["status"] == "off"
    assert not rig.socket.exists()


@pytest.mark.spec("CA-15", "RF-30", "RNF-15")
async def test_la_parada_espera_a_la_doble_en_vuelo(tmp_path):
    """SIGTERM con el bot disparando: las piernas terminan y quedan en el journal."""
    async with DaemonRig(tmp_path) as rig:
        gate = asyncio.Event()
        venue = rig.venues["nx"]
        real = venue.place_limit

        async def slow(req):
            await gate.wait()
            return await real(req)

        venue.place_limit = slow
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.orders.in_flight)
        rig.stop.set()
        await asyncio.sleep(0.05)
        assert not rig.daemon.done()  # espera a la pierna de Neoxa
        gate.set()
    sent = rig.events("order_sent")
    assert len(sent) == 2
    acks = {e["client_id"] for e in rig.events("order_ack")}
    assert {e["client_id"] for e in sent} <= acks


@pytest.mark.spec("CA-15", "RF-30", "RF-07", "RF-21")
async def test_las_piernas_llenas_no_paran_el_bot(tmp_path):
    """Visto en live el 2026-10-24 (d-68e3ed): con las dos piernas llenas, el
    daemon no se enteraba de los fills de NonKYC, cancelaba una orden ya llena
    ("Not found"), paraba el bot por `unsettled` y avisaba de una pierna colgada
    que no existía."""
    async with DaemonRig(tmp_path) as rig:
        rig.bot._limits = replace(BOT, fill_timeout_ms=2_000)
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (double,) = rig.orders.doubles.values()
        await rig.until(lambda: all(leg.order_id for leg in double.legs))
        for leg in double.legs:
            await rig.venues[leg.req.venue].simulate_fill(leg.order_id, D("10"))
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        (double,) = rig.orders.doubles.values()
        assert {leg.status for leg in double.legs} == {"filled"}
        rig.clock.now += 60_000  # pasado hanging_leg_after_s
        rig.engine.check_doubles()
        assert rig.events("cancel_sent") == [] and rig.events("hanging_leg") == []
        assert rig.bot.state.status != "halted"
