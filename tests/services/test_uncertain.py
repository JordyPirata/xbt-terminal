"""Envíos inciertos y reconciliación en OrderService (T-25, RNF-12, D-26).

El venue es un DryRunVenue con NonKYC 0.00720000 / 0.00730000 (ver test_orders).
"Respuesta perdida" = la orden se crea en el venue pero `place_limit` lanza
`UncertainError`, como un timeout después del POST."""

import pytest

from xbt_terminal.adapters.base import UncertainError, VenueError
from xbt_terminal.core.commands import DoubleOrder
from xbt_terminal.core.models import D, OrderRequest
from xbt_terminal.services.orders import (
    DoubleSent,
    OrderService,
    Rejected,
    Sent,
    Uncertain,
)

from . import test_double_orders as double
from .test_orders import LIMITS, NOW, Setup, buy, sell


class Rig(Setup):
    """Setup de test_orders con un reloj que se puede mover."""

    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, **kw)
        self.now = NOW
        ids = iter(f"t-{i:06d}" for i in range(1, 100))
        self.orders = OrderService(
            self.hub,
            {"nk": self.venue},
            self.journal,
            LIMITS,
            clock=lambda: self.now,
            new_client_id=lambda: next(ids),
        )

    def lose_reply(self, *, create=True):
        """El siguiente envío pierde la respuesta; con `create`, la orden sí se crea."""
        real = self.venue.place_limit

        async def lost(req):
            self.venue.place_limit = real
            if create:
                await real(req)
            raise UncertainError("NonKYC: sin respuesta (ReadTimeout)")

        self.venue.place_limit = lost

    async def send(self, cmd):
        return await self.orders.confirm(await self.orders.prepare(cmd))

    def events(self):
        return [ln["event"] for ln in self.lines()]


@pytest.mark.spec("RNF-12", "RF-11")
class TestEnvioIncierto:
    async def test_no_es_un_fallo(self, tmp_path):
        s = Rig(tmp_path)
        s.lose_reply()
        result = await s.send(buy())
        assert isinstance(result, Uncertain) and "ReadTimeout" in result.error
        assert s.events() == ["order_sent", "order_unknown"]
        assert s.lines()[1]["client_id"] == "t-000001"
        assert s.lines()[1]["data"] == {"error": "NonKYC: sin respuesta (ReadTimeout)"}
        assert list(s.orders.uncertain) == ["t-000001"]

    async def test_un_rechazo_claro_sigue_siendo_fallo(self, tmp_path):
        s = Rig(tmp_path)

        async def boom(req):
            raise VenueError("Insufficient funds")

        s.venue.place_limit = boom
        await s.send(buy())
        assert s.events() == ["order_sent", "order_error"]
        assert s.orders.uncertain == {}


@pytest.mark.spec("RNF-12", "RF-11")
class TestReconciliar:
    async def test_aparece_por_client_id(self, tmp_path):
        s = Rig(tmp_path)
        s.lose_reply()
        await s.send(buy())
        (r,) = await s.orders.reconcile()
        assert (r.client_id, r.outcome) == ("t-000001", "found")
        assert "dry-nk-1" in r.message
        ack = s.lines()[-1]
        assert (ack["event"], ack["client_id"]) == ("order_ack", "t-000001")
        assert ack["data"] == {"order_id": "dry-nk-1", "status": "open", "reconciled": True}
        assert s.orders.uncertain == {}

    async def test_la_reconciliada_cuenta_para_validar(self, tmp_path):
        """Una vez encontrada, una venta que la cruce se rechaza (self_cross)."""
        s = Rig(tmp_path)
        s.lose_reply()
        await s.send(buy(price="0.00725000"))
        await s.orders.reconcile()
        result = await s.orders.prepare(sell(price="0.00724000"))
        assert isinstance(result, Rejected) and "self_cross" in result.codes

    async def test_sin_rastro_sigue_incierta_hasta_60_s(self, tmp_path):
        s = Rig(tmp_path)
        s.lose_reply(create=False)
        await s.send(buy())
        s.now = NOW + 59_000
        assert await s.orders.reconcile() == []
        assert s.events() == ["order_sent", "order_unknown"]
        s.now = NOW + 61_000
        (r,) = await s.orders.reconcile()
        assert r.outcome == "missing"
        err = s.lines()[-1]
        assert (err["event"], err["client_id"]) == ("order_error", "t-000001")
        assert err["data"]["reconciled"] is True and "no existe" in err["data"]["error"]
        assert s.orders.uncertain == {}

    async def test_si_el_venue_no_responde_se_reintenta(self, tmp_path):
        s = Rig(tmp_path)
        s.lose_reply()
        await s.send(buy())

        async def down(since):
            raise VenueError("caído")

        real, s.venue.recent_orders = s.venue.recent_orders, down
        s.now = NOW + 120_000  # ni pasado el plazo se da por inexistente sin poder mirar
        assert await s.orders.reconcile() == []
        assert list(s.orders.uncertain) == ["t-000001"]
        s.venue.recent_orders = real
        (r,) = await s.orders.reconcile()
        assert r.outcome == "found"


@pytest.mark.spec("RNF-12")
class TestSinClientId:
    """Neoxa (PA-09): por lado, precio y cantidad, sin las órdenes ya conocidas."""

    async def test_una_igual_ya_conocida_no_se_confunde(self, tmp_path):
        s = Rig(tmp_path)
        s.venue.keeps_client_id = False
        first = await s.send(buy())
        assert isinstance(first, Sent)
        s.lose_reply()
        await s.send(buy())
        (r,) = await s.orders.reconcile()
        assert r.outcome == "found" and "dry-nk-2" in r.message

    async def test_una_igual_enviada_despues_por_la_terminal_no_se_confunde(self, tmp_path):
        s = Rig(tmp_path)
        s.venue.keeps_client_id = False
        s.lose_reply()
        await s.send(buy())
        assert isinstance(await s.send(buy()), Sent)  # dry-nk-2, con su ack
        (r,) = await s.orders.reconcile()
        assert r.outcome == "found" and "dry-nk-1" in r.message

    async def test_dos_candidatas_sigue_incierta_y_avisa_una_vez(self, tmp_path):
        s = Rig(tmp_path)
        s.venue.keeps_client_id = False
        s.lose_reply()
        await s.send(buy())
        # otra igual, creada fuera de la terminal en la misma ventana
        await s.venue.place_limit(OrderRequest("nk", "buy", D("0.01"), D("0.00725"), "x"))
        (r,) = await s.orders.reconcile()
        assert r.outcome == "ambiguous" and "2" in r.message
        assert await s.orders.reconcile() == []
        assert list(s.orders.uncertain) == ["t-000001"]
        assert s.events() == ["order_sent", "order_unknown"]


@pytest.mark.spec("RNF-12", "RF-07")
class TestOrdenDoble:
    async def test_la_pierna_incierta_queda_unknown_y_se_resuelve(self, tmp_path):
        s = double.Setup(tmp_path)
        real = s.venues["nx"].place_limit

        async def lost(req):
            await real(req)
            raise UncertainError("Neoxa: sin respuesta (ReadTimeout)")

        s.venues["nx"].place_limit = lost
        cmd = DoubleOrder(D("0.01"), "nk", D("0.00725000"), "nx", D("0.00726000"))
        result = await s.orders.confirm_double(await s.orders.prepare_double(cmd))
        assert isinstance(result, DoubleSent)
        assert (result.state.buy.status, result.state.sell.status) == ("open", "unknown")
        assert "ReadTimeout" in result.state.sell.error
        unknown = next(ln for ln in s.lines() if ln["event"] == "order_unknown")
        assert unknown["data"]["double"] == "d-000001"
        (r,) = await s.orders.reconcile()
        assert r.outcome == "found"
        leg = s.orders.doubles["d-000001"].sell
        assert (leg.status, leg.order_id, leg.error) == ("open", "dry-nx-1", None)
        ack = s.lines()[-1]
        assert ack["event"] == "order_ack" and ack["data"]["double"] == "d-000001"

    async def test_la_pierna_que_no_existe_queda_rechazada(self, tmp_path):
        s = double.Setup(tmp_path)

        async def lost(req):
            raise UncertainError("Neoxa: sin respuesta (ReadTimeout)")

        s.venues["nx"].place_limit = lost
        cmd = DoubleOrder(D("0.01"), "nk", D("0.00725000"), "nx", D("0.00726000"))
        await s.orders.confirm_double(await s.orders.prepare_double(cmd))
        s.now = NOW + 61_000
        (r,) = await s.orders.reconcile()
        assert r.outcome == "missing"
        assert s.orders.doubles["d-000001"].sell.status == "rejected"
