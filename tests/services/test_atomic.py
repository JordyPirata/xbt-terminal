"""Envíos atómicos por venue (T-26, RNF-13, D-27).

Mismo mercado que test_orders: NonKYC 0.00720000 / 0.00730000. Una compra a
0.00725 y una venta a 0.00724 se cruzan (`self_cross`): si las dos se validan a
la vez sin contar la otra, salen las dos."""

import asyncio

import pytest

from btcb2_terminal.core.commands import DoubleOrder
from btcb2_terminal.core.models import D
from btcb2_terminal.services.orders import DoubleSent, Rejected, Sent, Uncertain

from . import test_double_orders as double
from .test_orders import NOW, buy, sell
from .test_uncertain import Rig


def slow(venue, gate: asyncio.Event):
    """`place_limit` espera a `gate` antes de crear la orden: un envío en vuelo."""
    real = venue.place_limit

    async def held(req):
        await gate.wait()
        return await real(req)

    venue.place_limit = held


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.spec("RNF-13", "RF-10")
class TestMismoVenue:
    async def test_la_segunda_cuenta_la_que_esta_en_vuelo(self, tmp_path):
        s = Rig(tmp_path)
        first = await s.orders.prepare(buy(price="0.00725000"))
        second = await s.orders.prepare(sell(price="0.00724000"))
        gate = asyncio.Event()
        slow(s.venue, gate)
        a = asyncio.create_task(s.orders.confirm(first))
        b = asyncio.create_task(s.orders.confirm(second))
        await settle()
        assert not b.done()  # espera a que acabe el envío de la primera
        gate.set()
        ra, rb = await a, await b
        assert isinstance(ra, Sent)
        assert isinstance(rb, Rejected) and "self_cross" in rb.codes
        assert s.events() == ["order_sent", "order_ack", "reject_local"]

    async def test_una_incierta_cuenta_como_abierta(self, tmp_path):
        """Puede existir en el venue: hasta saberlo, cuenta (ante la duda, no se envía)."""
        s = Rig(tmp_path)
        s.lose_reply(create=False)
        assert isinstance(await s.send(buy(price="0.00725000")), Uncertain)
        result = await s.orders.prepare(sell(price="0.00724000"))
        assert isinstance(result, Rejected) and "self_cross" in result.codes

    async def test_resuelta_como_inexistente_deja_de_contar(self, tmp_path):
        s = Rig(tmp_path)
        s.lose_reply(create=False)
        await s.send(buy(price="0.00725000"))
        s.now = NOW + 61_000
        s.book("nk", "0.00720000", "0.00730000", ts=s.now)
        (r,) = await s.orders.reconcile()
        assert r.outcome == "missing"
        assert isinstance(await s.send(sell(price="0.00724000")), Sent)

    async def test_incierta_y_encontrada_no_cuenta_dos_veces(self, tmp_path):
        """Con la orden ya en el venue y el mismo client_id, una sola vez."""
        s = Rig(tmp_path)
        s.lose_reply()
        await s.send(buy(price="0.00725000"))
        open_orders = await s.orders._snapshot("nk", s.venue)
        assert [o.client_id for o in open_orders[0]] == ["t-000001"]


@pytest.mark.spec("RNF-13")
class TestVenuesDistintos:
    async def test_no_se_bloquean_entre_si(self, tmp_path):
        s = double.Setup(tmp_path)
        gate = asyncio.Event()
        slow(s.venues["nk"], gate)
        stuck = asyncio.create_task(s.orders.confirm(await s.orders.prepare(buy(venue="nk"))))
        await settle()
        nx = await s.orders.prepare(sell(venue="nx", price="0.00727000"))
        result = await asyncio.wait_for(s.orders.confirm(nx), 1)
        assert isinstance(result, Sent) and not stuck.done()
        gate.set()
        assert isinstance(await stuck, Sent)

    async def test_orden_doble_espera_a_los_dos_venues(self, tmp_path):
        s = double.Setup(tmp_path)
        gate = asyncio.Event()
        slow(s.venues["nx"], gate)
        stuck = asyncio.create_task(
            s.orders.confirm(await s.orders.prepare(sell(venue="nx", price="0.00727000")))
        )
        await settle()
        cmd = DoubleOrder(D("0.01"), "nk", D("0.00725000"), "nx", D("0.00726000"))
        pending = await s.orders.prepare_double(cmd)
        task = asyncio.create_task(s.orders.confirm_double(pending))
        await settle()
        assert not task.done()
        assert [e for e, _ in s.events()] == ["order_sent"]  # ni la pierna de nk sale
        gate.set()
        assert isinstance(await stuck, Sent)
        assert isinstance(await asyncio.wait_for(task, 1), DoubleSent)
