"""Cancelar desde OrderService (T-19, RF-08, CA-05): una orden, las de un venue
o todas, con plan para confirmar y `cancel_sent`/`cancel_ack` en el journal."""

from dataclasses import replace

import pytest

from btcb2_terminal.adapters.base import VenueError
from btcb2_terminal.core.commands import CancelAll, CancelOrder, CancelVenue
from btcb2_terminal.core.models import D, OrderRequest
from btcb2_terminal.services.orders import CancelPlan, CancelRefused

from .test_double_orders import Setup


async def place(s, venue, side="buy", price="0.00725000", qty="0.01", cid="t-x"):
    return await s.venues[venue].place_limit(OrderRequest(venue, side, D(qty), D(price), cid))


async def with_orders(tmp_path):
    s = Setup(tmp_path, nk_btc="0.005", nx_btcb2="0.5")
    a = await place(s, "nk", cid="t-a")
    b = await place(s, "nk", price="0.00724000", cid="t-b")
    c = await place(s, "nx", side="sell", price="0.00730000", cid="t-c")
    return s, (a, b, c)


@pytest.mark.spec("RF-08")
class TestPlan:
    async def test_una_orden_por_id(self, tmp_path):
        s, (a, _, _) = await with_orders(tmp_path)
        plan = await s.orders.plan_cancel(CancelOrder(a.order_id))
        assert isinstance(plan, CancelPlan) and plan.orders == {"nk": (a,)}
        assert "cancelar 1 orden" in plan.summary and a.order_id in plan.summary

    async def test_id_desconocido(self, tmp_path):
        s, _ = await with_orders(tmp_path)
        refused = await s.orders.plan_cancel(CancelOrder("nada"))
        assert (
            isinstance(refused, CancelRefused)
            and "no hay ninguna orden abierta nada" in refused.message
        )

    async def test_mismo_id_en_los_dos_venues_no_adivina(self, tmp_path):
        s, (a, _, c) = await with_orders(tmp_path)
        clash = replace(c, order_id=a.order_id)

        async def both():
            return [clash]

        s.venues["nx"].open_orders = both
        refused = await s.orders.plan_cancel(CancelOrder(a.order_id))
        assert isinstance(refused, CancelRefused) and "los dos venues" in refused.message

    async def test_las_de_un_venue(self, tmp_path):
        s, (a, b, _) = await with_orders(tmp_path)
        plan = await s.orders.plan_cancel(CancelVenue("nk"))
        assert set(plan.orders) == {"nk"} and set(plan.orders["nk"]) == {a, b}
        assert "2 órdenes en NonKYC" in plan.summary

    async def test_todas(self, tmp_path):
        s, _ = await with_orders(tmp_path)
        plan = await s.orders.plan_cancel(CancelAll())
        assert sum(len(v) for v in plan.orders.values()) == 3
        assert "3 órdenes" in plan.summary

    async def test_cancelar_todo_avisa_de_las_ordenes_ajenas(self, tmp_path):
        """cancel_all del venue cancela todas las del par, no solo las de la terminal."""
        s, _ = await with_orders(tmp_path)
        plan = await s.orders.plan_cancel(CancelAll())
        assert "también las que no se enviaron desde la terminal" in plan.summary

    async def test_nada_que_cancelar(self, tmp_path):
        s = Setup(tmp_path)
        refused = await s.orders.plan_cancel(CancelAll())
        assert isinstance(refused, CancelRefused) and "no hay órdenes abiertas" in refused.message

    async def test_venue_sin_keys(self, tmp_path):
        s, _ = await with_orders(tmp_path)
        del s.orders._venues["nx"]
        refused = await s.orders.plan_cancel(CancelVenue("nx"))
        assert isinstance(refused, CancelRefused) and "sin keys" in refused.message


@pytest.mark.spec("RF-08", "RF-11", "CA-05")
class TestEjecutar:
    async def test_una_orden(self, tmp_path):
        s, (a, b, _) = await with_orders(tmp_path)
        result = await s.orders.execute_cancel(await s.orders.plan_cancel(CancelOrder(a.order_id)))
        assert result.ok and result.remaining == {"nk": 0}
        assert [o.order_id for o in await s.venues["nk"].open_orders()] == [b.order_id]
        sent, ack = s.lines()
        assert (sent["event"], ack["event"]) == ("cancel_sent", "cancel_ack")
        assert sent["data"] == {"scope": "order", "order_ids": [a.order_id]}
        assert sent["client_id"] == "t-a" and ack["data"]["remaining"] == 0

    async def test_cancelar_todo_deja_cero_abiertas(self, tmp_path):
        """CA-05 con dos venues simulados."""
        s, _ = await with_orders(tmp_path)
        result = await s.orders.execute_cancel(await s.orders.plan_cancel(CancelAll()))
        assert result.ok and result.remaining == {"nk": 0, "nx": 0}
        for venue in s.venues.values():
            assert await venue.open_orders() == []
        assert sorted(e for e, _ in s.events()) == ["cancel_ack"] * 2 + ["cancel_sent"] * 2
        assert "quedan 0" in result.message

    async def test_un_venue_falla_y_el_otro_cancela(self, tmp_path):
        s, _ = await with_orders(tmp_path)

        async def boom():
            raise VenueError("Neoxa: HTTP 429")

        s.venues["nx"].cancel_all = boom
        result = await s.orders.execute_cancel(await s.orders.plan_cancel(CancelAll()))
        assert not result.ok and "Neoxa: HTTP 429" in result.message
        assert await s.venues["nk"].open_orders() == []
        assert ("error", "nx") in s.events() and ("cancel_ack", "nk") in s.events()

    async def test_si_queda_alguna_se_dice(self, tmp_path):
        s, _ = await with_orders(tmp_path)

        async def noop():
            return None  # el venue responde ok pero no cancela

        s.venues["nx"].cancel_all = noop
        result = await s.orders.execute_cancel(await s.orders.plan_cancel(CancelAll()))
        assert not result.ok and result.remaining["nx"] == 1
        assert "Neoxa: queda 1 abierta" in result.message
