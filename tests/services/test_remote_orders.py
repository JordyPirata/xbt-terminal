"""Órdenes por el socket del daemon (T-63, RF-31, D-66): el cliente prepara y
confirma por referencia; el daemon valida (RF-10), envía y lo apunta."""

import asyncio

import pytest

from xbt_terminal.core.commands import CancelAll, DoubleOrder, PlaceOrder
from xbt_terminal.core.models import D
from xbt_terminal.services.control import ControlServer
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.orders import (
    CancelPlan,
    CancelRefused,
    CancelResult,
    DoubleRejected,
    DoubleSent,
    Failed,
    Pending,
    PendingDouble,
    Rejected,
    Sent,
)
from xbt_terminal.services.remote import DaemonGone, DaemonRefused, RemoteEngine

from .test_double_orders import Setup as DoubleSetup
from .test_uncertain import Rig


async def until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def buy(qty="0.01", price="0.00725000"):
    return PlaceOrder("nk", "buy", D(qty), D(price))


class Daemon:
    def __init__(self, tmp_path, setup=None, ttl_s=60.0):
        self.s = setup or Rig(tmp_path)
        s = self.s
        self.engine = Engine(s.hub, mode="dry-run", orders=s.orders, journal=s.journal)
        self.path = tmp_path / "o.sock"
        self.server = ControlServer(self.engine, self.path, every_s=0.05, proposal_ttl_s=ttl_s)

    async def __aenter__(self):
        await self.server.start()
        return self

    async def __aexit__(self, *exc):
        await self.server.aclose()


class Linked:
    def __init__(self, daemon):
        self.daemon = daemon
        self.remote = RemoteEngine(daemon.path, has_bot=False, retry_s=0.02)
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.remote.run())
        await until(lambda: self.remote.connected and self.remote.snapshot is not None)
        return self.remote

    async def __aexit__(self, *exc):
        self.task.cancel()
        await self.remote.aclose()


def events(lines):
    return [ln["event"] for ln in lines]


@pytest.mark.spec("RF-31", "RF-06", "CA-16")
class TestOrdenSimple:
    async def test_prepara_confirma_y_llega_al_venue(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:
            pending = await remote.orders.prepare(buy())
            assert isinstance(pending, Pending) and pending.req.venue == "nk"
            assert "BUY" in pending.summary
            result = await remote.orders.confirm(pending)
            assert isinstance(result, Sent) and result.order.status == "open"
            assert events(d.s.lines())[-2:] == ["order_sent", "order_ack"]
            assert len(await d.s.venue.open_orders()) == 1

    async def test_el_daemon_valida_y_rechaza(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:
            rejected = await remote.orders.prepare(buy(qty="100"))
            assert isinstance(rejected, Rejected) and rejected.codes
            assert events(d.s.lines()) == ["reject_local"]  # lo apunta el daemon

    async def test_la_propuesta_vale_una_sola_vez(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:
            pending = await remote.orders.prepare(buy())
            token = remote.orders._tokens[id(pending)][1]
            await remote.orders.confirm(pending)
            with pytest.raises(DaemonRefused, match="caducada o ya usada"):
                await remote.request("confirm", {"token": token})
            assert events(d.s.lines()).count("order_sent") == 1

    async def test_una_propuesta_caducada_no_se_envia(self, tmp_path):
        async with Daemon(tmp_path, ttl_s=0.01) as d, Linked(d) as remote:
            pending = await remote.orders.prepare(buy())
            await asyncio.sleep(0.05)
            result = await remote.orders.confirm(pending)
            assert isinstance(result, Failed) and result.local
            assert "caducada" in result.error
            assert d.s.lines() == []

    async def test_un_token_desconocido(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:
            with pytest.raises(DaemonRefused, match="caducada o ya usada"):
                await remote.request("confirm", {"token": "inventado"})

    async def test_dos_clientes_con_el_mismo_token_solo_uno_envia(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as a, Linked(d) as b:
            pending = await a.orders.prepare(buy())
            token = a.orders._tokens[id(pending)][1]
            results = await asyncio.gather(
                a.request("confirm", {"token": token}),
                b.request("confirm", {"token": token}),
                return_exceptions=True,
            )
            assert sum(isinstance(r, DaemonRefused) for r in results) == 1
            assert events(d.s.lines()).count("order_sent") == 1

    async def test_en_solo_lectura_no_hay_ordenes(self, tmp_path):
        s = Rig(tmp_path)
        s.orders = None
        async with Daemon(tmp_path, setup=s) as d, Linked(d) as remote:
            with pytest.raises(DaemonRefused, match="solo lectura"):
                await remote.orders.prepare(buy())


@pytest.mark.spec("RF-31", "RF-07", "CA-16")
class TestOrdenDoble:
    async def test_prepara_y_confirma(self, tmp_path):
        s = DoubleSetup(tmp_path)
        async with Daemon(tmp_path, setup=s) as d, Linked(d) as remote:
            seen = []
            remote.orders.subscribe_doubles(seen.append)
            cmd = DoubleOrder(D("0.01"), "nk", D("0.00730000"), "nx", D("0.00722000"))
            pending = await remote.orders.prepare_double(cmd)
            assert isinstance(pending, PendingDouble) and pending.id == "d-000001"
            sent = await remote.orders.confirm_double(pending)
            assert isinstance(sent, DoubleSent) and len(sent.state.legs) == 2
            await until(lambda: "d-000001" in remote.doubles)
            await until(lambda: "d-000001" in seen)

    async def test_rechazada_por_el_daemon(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:  # nx sin keys
            cmd = DoubleOrder(D("0.01"), "nk", D("0.00730000"), "nx", D("0.00722000"))
            rejected = await remote.orders.prepare_double(cmd)
            assert isinstance(rejected, DoubleRejected) and rejected.codes


@pytest.mark.spec("RF-31", "RF-08", "CA-16")
class TestCancelar:
    async def test_planea_y_cancela(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:
            await remote.orders.confirm(await remote.orders.prepare(buy()))
            plan = await remote.orders.plan_cancel(CancelAll())
            assert isinstance(plan, CancelPlan) and plan.scope == "all"
            result = await remote.orders.execute_cancel(plan)
            assert isinstance(result, CancelResult) and result.ok
            assert await d.s.venue.open_orders() == []

    async def test_nada_que_cancelar(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:
            refused = await remote.orders.plan_cancel(CancelAll())
            assert isinstance(refused, CancelRefused)


@pytest.mark.spec("RF-31")
class TestSinDaemon:
    async def test_se_va_con_una_peticion_en_vuelo(self, tmp_path):
        async with Daemon(tmp_path) as d, Linked(d) as remote:
            gate = asyncio.Event()
            real = d.s.orders.prepare

            async def slow(cmd):
                await gate.wait()
                return await real(cmd)

            d.s.orders.prepare = slow
            asking = asyncio.create_task(remote.orders.prepare(buy()))
            await asyncio.sleep(0.05)
            await d.server.aclose()
            with pytest.raises(DaemonGone):
                await asking
            gate.set()

    async def test_sin_conexion_no_se_manda(self, tmp_path):
        remote = RemoteEngine(tmp_path / "no.sock", has_bot=False)
        with pytest.raises(DaemonGone):
            await remote.orders.prepare(buy())
