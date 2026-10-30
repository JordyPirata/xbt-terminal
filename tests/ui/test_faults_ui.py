"""Fallos aislados en la terminal (T-27, RNF-14, D-28): un bug no la cierra,
sale en rojo y queda contado en la barra de estado."""

import pytest

from xbt_terminal.ui.screens.confirm import ConfirmScreen
from xbt_terminal.ui.status import StatusBar

from ..services.test_faults import Buggy, full_disk
from .test_orders_ui import Rig, ready, type_command, until


@pytest.mark.spec("RNF-14", "RF-14")
class TestFallos:
    async def test_un_bug_en_un_venue_no_cierra_la_terminal(self, tmp_path):
        rig = Rig(tmp_path)
        rig.feeds["nx"] = rig.hub._venues["nx"] = Buggy("nx")  # falla una vez
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)  # NonKYC sigue con su book
            await until(pilot, lambda: "fallo en mercado de Neoxa" in rig.app.last_message)
            assert rig.app.last_error and "KeyError" in rig.app.last_message
            await until(pilot, lambda: "✖ 1 fallo" in rig.app.query_one(StatusBar).text)
            await until(pilot, lambda: rig.hub.state["nx"].ready)  # reintentado
            assert rig.app.is_running

    async def test_journal_lleno_tras_enviar(self, tmp_path):
        rig = Rig(tmp_path)
        full_disk(rig.app.orders._journal, "order_ack")
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "sin order_ack" in rig.app.last_message)
            assert rig.app.last_error and "No space left" in rig.app.last_message
            assert [o.order_id for o in await rig.venue.open_orders()] == ["dry-nk-1"]
            await until(pilot, lambda: "✖ 1 fallo" in rig.app.query_one(StatusBar).text)
            assert rig.app.is_running

    async def test_sin_journal_no_se_envia_y_lo_dice(self, tmp_path):
        rig = Rig(tmp_path)
        full_disk(rig.app.orders._journal, "order_sent")
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "no se envió" in rig.app.last_message)
            assert "error del venue" not in rig.app.last_message
            assert await rig.venue.open_orders() == []

    async def test_un_bug_al_preparar_no_cierra_la_terminal(self, tmp_path):
        rig = Rig(tmp_path)

        async def boom(cmd):
            raise RuntimeError("bug al preparar")

        rig.app.orders.prepare = boom
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: "bug al preparar" in rig.app.last_message)
            assert rig.app.last_error and rig.app.is_running
            await until(pilot, lambda: "✖ 1 fallo" in rig.app.query_one(StatusBar).text)

    async def test_un_bug_al_confirmar_no_cierra_la_terminal(self, tmp_path):
        rig = Rig(tmp_path)

        async def boom(pending):
            raise RuntimeError("bug al confirmar")

        rig.app.orders.confirm = boom
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await type_command(pilot, rig.app, "buy nk 0.01 0.00725000")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "bug al confirmar" in rig.app.last_message)
            assert rig.app.is_running
