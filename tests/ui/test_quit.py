"""Exiting the terminal (T-28, RNF-15, D-29): no half sends and, in live, confirming if
open orders remain."""

import pytest

from xbt_terminal.services.account import AccountService
from xbt_terminal.ui.screens.confirm import ConfirmScreen

from ..services.test_account import FakePrivate, order
from ..services.test_exit import slow
from ..services.test_orders import buy
from .test_orders_ui import NOW, Rig, ready, until


def with_open_orders(rig, mode, order_count):
    """The account sees `n` open orders on NonKYC; the app, in `mode`."""
    venue = FakePrivate()
    venue.push(*(order(f"o-{i}") for i in range(order_count)))
    account = AccountService({"nk": venue}, retry_s=0.01, clock=lambda: NOW)
    rig.app.account = rig.app.engine.account = account  # the engine runs it (D-61)
    rig.app.mode = mode


async def opened(rig, pilot, order_count):
    await until(pilot, lambda: len(rig.app.account.state["nk"].orders) == order_count)


@pytest.mark.spec("RNF-15", "RF-11")
class TestInFlight:
    async def test_worker_cancelled_on_exit_leaves_no_half_send(self, tmp_path):
        """On exit, Textual cancels the workers: the send goes on and reaches the journal."""
        rig = Rig(tmp_path)
        slow(rig.venue, "place_limit", delay=0.2)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            pending = await rig.app.orders.prepare(buy())
            rig.app.run_worker(rig.app.orders.confirm(pending), exit_on_error=False)
            await until(pilot, lambda: rig.events() == ["order_sent"])
            assert "1 envío" in await quit_message(rig, pilot)
        assert rig.events() == ["order_sent", "order_ack"]
        assert [order.order_id for order in await rig.venue.open_orders()] == ["dry-nk-1"]


async def quit_message(rig, pilot):
    await pilot.press("q")
    return rig.app.last_message


@pytest.mark.spec("RNF-15")
class TestConfirmOnExit:
    async def test_live_with_open_orders_asks_confirmation(self, tmp_path):
        rig = Rig(tmp_path)
        with_open_orders(rig, "live", 2)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await opened(rig, pilot, 2)
            await pilot.press("q")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "2 órdenes abiertas" in rig.app.screen.summary
            assert "SALIR" in rig.app.screen.text
            await pilot.press("enter")  # by default, no exit
            await until(pilot, lambda: not isinstance(rig.app.screen, ConfirmScreen))
            assert rig.app.is_running
            await pilot.press("q")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: not rig.app.is_running)

    async def test_single_order_in_singular(self, tmp_path):
        rig = Rig(tmp_path)
        with_open_orders(rig, "live", 1)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await opened(rig, pilot, 1)
            await pilot.press("q")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "1 orden abierta" in rig.app.screen.summary

    async def test_live_without_orders_exits_directly(self, tmp_path):
        rig = Rig(tmp_path)
        with_open_orders(rig, "live", 0)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await until(pilot, lambda: not rig.app.is_running)

    async def test_dry_run_with_orders_exits_directly(self, tmp_path):
        """Simulated: they stay on no exchange."""
        rig = Rig(tmp_path)
        with_open_orders(rig, "dry-run", 2)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await opened(rig, pilot, 2)
            await pilot.press("q")
            await until(pilot, lambda: not rig.app.is_running)
