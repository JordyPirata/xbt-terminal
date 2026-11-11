"""Orders over the daemon socket (T-63, RF-31, D-66): the client prepares and confirms
by reference; the daemon validates (RF-10), sends and notes it."""

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
from xbt_terminal.services.remote import DaemonGoneError, DaemonRefusedError, RemoteEngine

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
        env = self.s
        self.engine = Engine(env.hub, mode="dry-run", orders=env.orders, journal=env.journal)
        self.path = tmp_path / "o.sock"
        self.server = ControlServer(self.engine, self.path, every_s=0.05, proposal_ttl_s=ttl_s)

    async def __aenter__(self):
        await self.server.start()
        return self

    async def __aexit__(self, *error):
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

    async def __aexit__(self, *error):
        self.task.cancel()
        await self.remote.aclose()


def events(lines):
    return [ln["event"] for ln in lines]


@pytest.mark.spec("RF-31", "RF-06", "CA-16")
class TestSimpleOrder:
    async def test_prepares_confirms_and_reaches_venue(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:
            pending = await remote.orders.prepare(buy())
            assert isinstance(pending, Pending) and pending.req.venue == "nk"
            assert "BUY" in pending.summary
            result = await remote.orders.confirm(pending)
            assert isinstance(result, Sent) and result.order.status == "open"
            assert events(daemon.s.lines())[-2:] == ["order_sent", "order_ack"]
            assert len(await daemon.s.venue.open_orders()) == 1

    async def test_daemon_validates_and_rejects(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:
            rejected = await remote.orders.prepare(buy(qty="100"))
            assert isinstance(rejected, Rejected) and rejected.codes
            assert events(daemon.s.lines()) == ["reject_local"]  # noted by the daemon

    async def test_proposal_valid_once(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:
            pending = await remote.orders.prepare(buy())
            token = remote.orders._tokens[id(pending)][1]
            await remote.orders.confirm(pending)
            with pytest.raises(DaemonRefusedError, match="caducada o ya usada"):
                await remote.request("confirm", {"token": token})
            assert events(daemon.s.lines()).count("order_sent") == 1

    async def test_expired_proposal_not_sent(self, tmp_path):
        async with Daemon(tmp_path, ttl_s=0.01) as daemon, Linked(daemon) as remote:
            pending = await remote.orders.prepare(buy())
            await asyncio.sleep(0.05)
            result = await remote.orders.confirm(pending)
            assert isinstance(result, Failed) and result.local
            assert "caducada" in result.error
            assert daemon.s.lines() == []

    async def test_unknown_token(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:
            with pytest.raises(DaemonRefusedError, match="caducada o ya usada"):
                await remote.request("confirm", {"token": "inventado"})

    async def test_two_clients_same_token_only_one_sends(self, tmp_path):
        async with (
            Daemon(tmp_path) as daemon,
            Linked(daemon) as first_client,
            Linked(daemon) as second_client,
        ):
            pending = await first_client.orders.prepare(buy())
            token = first_client.orders._tokens[id(pending)][1]
            results = await asyncio.gather(
                first_client.request("confirm", {"token": token}),
                second_client.request("confirm", {"token": token}),
                return_exceptions=True,
            )
            assert sum(isinstance(result, DaemonRefusedError) for result in results) == 1
            assert events(daemon.s.lines()).count("order_sent") == 1

    async def test_read_only_has_no_orders(self, tmp_path):
        env = Rig(tmp_path)
        env.orders = None
        async with Daemon(tmp_path, setup=env) as daemon, Linked(daemon) as remote:
            with pytest.raises(DaemonRefusedError, match="solo lectura"):
                await remote.orders.prepare(buy())


@pytest.mark.spec("RF-31", "RF-07", "CA-16")
class TestDoubleOrder:
    async def test_prepares_and_confirms(self, tmp_path):
        env = DoubleSetup(tmp_path)
        async with Daemon(tmp_path, setup=env) as daemon, Linked(daemon) as remote:
            seen = []
            remote.orders.subscribe_doubles(seen.append)
            command = DoubleOrder(D("0.01"), "nk", D("0.00730000"), "nx", D("0.00722000"))
            pending = await remote.orders.prepare_double(command)
            assert isinstance(pending, PendingDouble) and pending.id == "d-000001"
            sent = await remote.orders.confirm_double(pending)
            assert isinstance(sent, DoubleSent) and len(sent.state.legs) == 2
            await until(lambda: "d-000001" in remote.doubles)
            await until(lambda: "d-000001" in seen)

    async def test_rejected_by_daemon(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:  # nx without keys
            command = DoubleOrder(D("0.01"), "nk", D("0.00730000"), "nx", D("0.00722000"))
            rejected = await remote.orders.prepare_double(command)
            assert isinstance(rejected, DoubleRejected) and rejected.codes


@pytest.mark.spec("RF-31", "RF-08", "CA-16")
class TestCancel:
    async def test_plans_and_cancels(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:
            await remote.orders.confirm(await remote.orders.prepare(buy()))
            plan = await remote.orders.plan_cancel(CancelAll())
            assert isinstance(plan, CancelPlan) and plan.scope == "all"
            result = await remote.orders.execute_cancel(plan)
            assert isinstance(result, CancelResult) and result.ok
            assert await daemon.s.venue.open_orders() == []

    async def test_nothing_to_cancel(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:
            refused = await remote.orders.plan_cancel(CancelAll())
            assert isinstance(refused, CancelRefused)


@pytest.mark.spec("RF-31")
class TestWithoutDaemon:
    async def test_leaves_with_request_in_flight(self, tmp_path):
        async with Daemon(tmp_path) as daemon, Linked(daemon) as remote:
            gate = asyncio.Event()
            real = daemon.s.orders.prepare

            async def slow(command):
                await gate.wait()
                return await real(command)

            daemon.s.orders.prepare = slow
            asking = asyncio.create_task(remote.orders.prepare(buy()))
            await asyncio.sleep(0.05)
            await daemon.server.aclose()
            with pytest.raises(DaemonGoneError):
                await asking
            gate.set()

    async def test_unconnected_sends_nothing(self, tmp_path):
        remote = RemoteEngine(tmp_path / "no.sock", has_bot=False)
        with pytest.raises(DaemonGoneError):
            await remote.orders.prepare(buy())
