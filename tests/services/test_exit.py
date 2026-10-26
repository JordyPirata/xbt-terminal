"""Salida segura en OrderService (T-28, RNF-15, D-29): lo que ya salió hacia el
venue termina y queda en el journal aunque quien lo esperaba se cancele."""

import asyncio

import pytest

from xbt_terminal.core.commands import CancelVenue

from .test_orders import Setup, buy


def slow(venue, method, delay=0.05):
    real = getattr(venue, method)

    async def held(*args):
        await asyncio.sleep(delay)
        return await real(*args)

    setattr(venue, method, held)


def events(s):
    return [ln["event"] for ln in s.lines()]


@pytest.mark.spec("RNF-15", "RF-11")
class TestEnVuelo:
    async def test_un_envio_cancelado_a_medias_termina_igual(self, tmp_path):
        s = Setup(tmp_path)
        pending = await s.orders.prepare(buy())
        slow(s.venue, "place_limit")
        task = asyncio.create_task(s.orders.confirm(pending))
        while events(s) != ["order_sent"]:
            await asyncio.sleep(0.001)
        assert s.orders.in_flight == 1
        task.cancel()  # como al cerrar la app con el envío en vuelo
        await asyncio.gather(task, return_exceptions=True)
        await s.orders.drain()
        assert events(s) == ["order_sent", "order_ack"]
        assert [o.order_id for o in await s.venue.open_orders()] == ["dry-nk-1"]
        assert s.orders.in_flight == 0

    async def test_una_cancelacion_a_medias_termina_igual(self, tmp_path):
        s = Setup(tmp_path)
        await s.orders.confirm(await s.orders.prepare(buy()))
        plan = await s.orders.plan_cancel(CancelVenue("nk"))
        slow(s.venue, "cancel_all")
        task = asyncio.create_task(s.orders.execute_cancel(plan))
        while "cancel_sent" not in events(s):
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await s.orders.drain()
        assert events(s)[-2:] == ["cancel_sent", "cancel_ack"]
        assert await s.venue.open_orders() == []

    async def test_sin_nada_en_vuelo_drain_vuelve_ya(self, tmp_path):
        s = Setup(tmp_path)
        await asyncio.wait_for(s.orders.drain(), 0.1)
