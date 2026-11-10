"""CA-16 (T-65, RF-31, D-66): with simulated venues and a dry-run daemon, the client TUI
places and confirms a simple and a double order and cancels; another client sees the
same; on closing the TUI the orders stay in the daemon."""

import asyncio

import pytest

from xbt_terminal.core.models import D
from xbt_terminal.services.control import ControlServer
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.remote import RemoteEngine
from xbt_terminal.services.wire import ClientView
from xbt_terminal.ui.app import ClientServices, XbtApp
from xbt_terminal.ui.screens.confirm import ConfirmScreen

from ..services.test_double_orders import Setup
from .test_orders_ui import type_command, until


class DaemonRig:
    """Two DryRunVenues (NonKYC with BTC, Neoxa with BTCB2) behind the socket."""

    def __init__(self, tmp_path):
        self.s = Setup(tmp_path)
        self.engine = Engine(
            self.s.hub, mode="dry-run", orders=self.s.orders, journal=self.s.journal
        )
        self.path = tmp_path / "ca16.sock"
        self.server = ControlServer(self.engine, self.path, every_s=0.05, book_every_s=0.01)

    async def __aenter__(self):
        await self.server.start()
        return self

    async def __aexit__(self, *error):
        await self.server.aclose()

    async def open_orders(self):
        return {venue_id: await v.open_orders() for venue_id, v in self.s.venues.items()}


def client_app(rig) -> XbtApp:
    remote = RemoteEngine(rig.path, has_bot=False, retry_s=0.02)
    services = ClientServices(
        orders=remote.orders, account=remote.account, results=remote.results,
        deposits=remote.deposits, rebalance=remote.rebalance, remote=remote,
    )  # fmt: skip
    return XbtApp(
        remote.market, view=ClientView(15, "1m", {}, None, None), mode="dry-run",
        engine=Engine(remote.market, mode="dry-run"), services=services, run_hub=False,
    )  # fmt: skip


async def confirm(pilot, app):
    await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
    await pilot.press("y")


@pytest.mark.spec("CA-16", "RF-31", "RF-06", "RF-07", "RF-08")
async def test_trades_over_socket_and_orders_stay_after_closing(tmp_path):
    async with DaemonRig(tmp_path) as rig:
        app = client_app(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            await until(pilot, lambda: app.remote.connected and app.hub.state["nk"].book.valid)
            # simple: buy on NonKYC below the book, stays open
            await type_command(pilot, app, "buy nk 0.01 0.00710000")
            await confirm(pilot, app)
            await until(pilot, lambda: app.last_message.startswith("enviada"))
            # double: buy on NonKYC and sell on Neoxa, crossing both books
            await type_command(pilot, app, "dbl 0.01 nk 0.00730000 nx 0.00722000")
            await confirm(pilot, app)
            await until(pilot, lambda: "orden doble d-000001 enviada" in app.last_message)
            assert "d-000001" in rig.s.orders.doubles  # kept by the daemon

            # another client sees the same orders and legs
            other = RemoteEngine(rig.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(other.run())
            await until(pilot, lambda: other.snapshot is not None and "d-000001" in other.doubles)
            assert other.doubles["d-000001"] == rig.s.orders.doubles["d-000001"]
            task.cancel()
            await other.aclose()

            await pilot.press("escape", "q")  # releases the command line and exits
            await until(pilot, lambda: not app.is_running)

        # the TUI left: the simple order stays open in the daemon
        await asyncio.sleep(0.05)
        open_now = await rig.open_orders()
        assert D("0.00710000") in {o.price for o in open_now["nk"]}
        events = [ln["event"] for ln in rig.s.lines()]
        assert events.count("order_sent") == 3  # the simple one and both legs


@pytest.mark.spec("CA-16", "RF-31", "RF-08")
async def test_cancels_over_the_socket(tmp_path):
    async with DaemonRig(tmp_path) as rig:
        app = client_app(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            await until(pilot, lambda: app.remote.connected and app.hub.state["nk"].book.valid)
            await type_command(pilot, app, "buy nk 0.01 0.00710000")
            await confirm(pilot, app)
            await until(pilot, lambda: app.last_message.startswith("enviada"))
            await type_command(pilot, app, "cancel all")
            await confirm(pilot, app)
            await until(pilot, lambda: "cancelado" in app.last_message)
        assert (await rig.open_orders()) == {"nk": [], "nx": []}
