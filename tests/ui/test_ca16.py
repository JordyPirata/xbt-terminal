"""CA-16 (T-65, RF-31, D-66): con venues simulados y un daemon en dry-run, la TUI
cliente coloca y confirma una orden simple y una doble y cancela; otro cliente
ve lo mismo; al cerrar la TUI las órdenes siguen en el daemon."""

import asyncio

import pytest

from xbt_terminal.core.models import D
from xbt_terminal.services.control import ControlServer
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.remote import RemoteEngine
from xbt_terminal.ui.app import ConfirmScreen, XbtApp

from ..services.test_double_orders import Setup
from .test_orders_ui import type_command, until


class DaemonRig:
    """Dos DryRunVenue (NonKYC con BTC, Neoxa con BTCB2) detrás del socket."""

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

    async def __aexit__(self, *exc):
        await self.server.aclose()

    async def open_orders(self):
        return {vid: await v.open_orders() for vid, v in self.s.venues.items()}


def client_app(rig) -> XbtApp:
    remote = RemoteEngine(rig.path, has_bot=False, retry_s=0.02)
    return XbtApp(
        remote.market, run_hub=False, book_levels=15, timeframe="1m", mode="dry-run",
        account=remote.account, orders=remote.orders, results=remote.results,
        deposits=remote.deposits, rebalance=remote.rebalance, remote=remote,
        engine=Engine(remote.market, mode="dry-run"),
    )  # fmt: skip


async def confirm(pilot, app):
    await until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
    await pilot.press("y")


@pytest.mark.spec("CA-16", "RF-31", "RF-06", "RF-07", "RF-08")
async def test_opera_por_el_socket_y_al_cerrar_las_ordenes_siguen(tmp_path):
    async with DaemonRig(tmp_path) as rig:
        app = client_app(rig)
        async with app.run_test(size=(160, 45)) as pilot:
            await until(pilot, lambda: app.remote.connected and app.hub.state["nk"].book.valid)
            # simple: compra en NonKYC por debajo del book, queda abierta
            await type_command(pilot, app, "buy nk 0.01 0.00710000")
            await confirm(pilot, app)
            await until(pilot, lambda: app.last_message.startswith("enviada"))
            # doble: compra en NonKYC y venta en Neoxa, cruzando los dos books
            await type_command(pilot, app, "dbl 0.01 nk 0.00730000 nx 0.00722000")
            await confirm(pilot, app)
            await until(pilot, lambda: "orden doble d-000001 enviada" in app.last_message)
            assert "d-000001" in rig.s.orders.doubles  # la lleva el daemon

            # otro cliente ve las mismas órdenes y piernas
            other = RemoteEngine(rig.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(other.run())
            await until(pilot, lambda: other.snapshot is not None and "d-000001" in other.doubles)
            assert other.doubles["d-000001"] == rig.s.orders.doubles["d-000001"]
            task.cancel()
            await other.aclose()

            await pilot.press("escape", "q")  # suelta la línea de comandos y sale
            await until(pilot, lambda: not app.is_running)

        # la TUI se fue: la orden simple sigue abierta en el daemon
        await asyncio.sleep(0.05)
        open_now = await rig.open_orders()
        assert D("0.00710000") in {o.price for o in open_now["nk"]}
        events = [ln["event"] for ln in rig.s.lines()]
        assert events.count("order_sent") == 3  # la simple y las dos piernas


@pytest.mark.spec("CA-16", "RF-31", "RF-08")
async def test_cancela_por_el_socket(tmp_path):
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
