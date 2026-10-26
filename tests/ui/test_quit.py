"""Salir de la terminal (T-28, RNF-15, D-29): sin envíos a medias y, en live,
confirmando si quedan órdenes abiertas."""

import pytest

from btcb2_terminal.services.account import AccountService
from btcb2_terminal.ui.app import ConfirmScreen

from ..services.test_account import FakePrivate, order
from ..services.test_exit import slow
from ..services.test_orders import buy
from .test_orders_ui import NOW, Rig, ready, until


def with_open_orders(rig, mode, n):
    """La cuenta ve `n` órdenes abiertas en NonKYC; la app, en `mode`."""
    venue = FakePrivate()
    venue.push(*(order(f"o-{i}") for i in range(n)))
    account = AccountService({"nk": venue}, retry_s=0.01, clock=lambda: NOW)
    rig.app.account = rig.app.engine.account = account  # el motor la corre (D-61)
    rig.app.mode = mode


async def opened(rig, pilot, n):
    await until(pilot, lambda: len(rig.app.account.state["nk"].orders) == n)


@pytest.mark.spec("RNF-15", "RF-11")
class TestEnVuelo:
    async def test_un_worker_cancelado_al_salir_no_deja_el_envio_a_medias(self, tmp_path):
        """Al salir, Textual cancela los workers: el envío sigue y llega al journal."""
        rig = Rig(tmp_path)
        slow(rig.venue, "place_limit", delay=0.2)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            pending = await rig.app.orders.prepare(buy())
            rig.app.run_worker(rig.app.orders.confirm(pending), exit_on_error=False)
            await until(pilot, lambda: rig.events() == ["order_sent"])
            assert "1 envío" in await quit_message(rig, pilot)
        assert rig.events() == ["order_sent", "order_ack"]
        assert [o.order_id for o in await rig.venue.open_orders()] == ["dry-nk-1"]


async def quit_message(rig, pilot):
    await pilot.press("q")
    return rig.app.last_message


@pytest.mark.spec("RNF-15")
class TestConfirmarAlSalir:
    async def test_live_con_ordenes_abiertas_pide_confirmacion(self, tmp_path):
        rig = Rig(tmp_path)
        with_open_orders(rig, "live", 2)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await opened(rig, pilot, 2)
            await pilot.press("q")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "2 órdenes abiertas" in rig.app.screen.summary
            assert "SALIR" in rig.app.screen.text
            await pilot.press("enter")  # por defecto, no se sale
            await until(pilot, lambda: not isinstance(rig.app.screen, ConfirmScreen))
            assert rig.app.is_running
            await pilot.press("q")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: not rig.app.is_running)

    async def test_una_sola_orden_en_singular(self, tmp_path):
        rig = Rig(tmp_path)
        with_open_orders(rig, "live", 1)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await opened(rig, pilot, 1)
            await pilot.press("q")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "1 orden abierta" in rig.app.screen.summary

    async def test_live_sin_ordenes_sale_directo(self, tmp_path):
        rig = Rig(tmp_path)
        with_open_orders(rig, "live", 0)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await until(pilot, lambda: not rig.app.is_running)

    async def test_dry_run_con_ordenes_sale_directo(self, tmp_path):
        """Simuladas: no quedan en ningún exchange."""
        rig = Rig(tmp_path)
        with_open_orders(rig, "dry-run", 2)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await opened(rig, pilot, 2)
            await pilot.press("q")
            await until(pilot, lambda: not rig.app.is_running)
