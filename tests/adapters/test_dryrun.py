"""DryRunVenue (D-13): órdenes simuladas en memoria con saldos de la config;
los datos de mercado los da el venue real."""

import asyncio

import pytest

from xbt_terminal.adapters.base import Venue, VenueError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.core.models import Balance, D, Order, OrderRequest

from ..fakes import FakeFeed, spec


def balances(btc="0.005", btcb2="0.5"):
    return {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D(btcb2))}


def make(**kw):
    return DryRunVenue(FakeFeed("nk"), balances(**kw), clock=lambda: 1_000)


def req(side="buy", qty="0.01", price="0.00730000", cid="t-abc123"):
    return OrderRequest("nk", side, D(qty), D(price), cid)


@pytest.mark.spec("RF-13", "RNF-09")
class TestInterfaz:
    def test_cumple_venue(self):
        assert isinstance(make(), Venue)
        assert make().id == "nk"

    async def test_mercado_delegado_al_venue_real(self):
        venue = make()
        assert (await venue.market()) == spec("nk")
        assert venue.inner.calls == ["market"]


@pytest.mark.spec("RF-13")
class TestOrdenes:
    async def test_compra_abre_orden_y_bloquea_btc_con_fee(self):
        venue = make()
        order = await venue.place_limit(req())
        assert order == Order(
            "nk", "dry-nk-1", "t-abc123", "buy", D("0.00730000"), D("0.01"), D("0"), "open", 1_000
        )
        bal = await venue.balances()
        locked = D("0.01") * D("0.00730000") * D("1.002")  # taker 0.2 % del spec
        assert bal["BTC"].locked == locked and bal["BTC"].available == D("0.005") - locked
        assert bal["BTCB2"].available == D("0.5")
        assert await venue.open_orders() == [order]

    async def test_venta_bloquea_btcb2(self):
        venue = make()
        await venue.place_limit(req("sell", qty="0.2"))
        bal = await venue.balances()
        assert (bal["BTCB2"].available, bal["BTCB2"].locked) == (D("0.3"), D("0.2"))
        assert bal["BTC"].locked == 0

    async def test_ids_correlativos(self):
        venue = make()
        a = await venue.place_limit(req(cid="t-1"))
        b = await venue.place_limit(req(cid="t-2"))
        assert (a.order_id, b.order_id) == ("dry-nk-1", "dry-nk-2")

    async def test_sin_saldo_simulado_se_rechaza(self):
        venue = make(btc="0.00001")
        with pytest.raises(VenueError, match="saldo insuficiente"):
            await venue.place_limit(req())
        assert await venue.open_orders() == []

    async def test_cancelar_libera_el_saldo(self):
        venue = make()
        order = await venue.place_limit(req())
        await venue.cancel(order.order_id)
        assert await venue.open_orders() == []
        bal = await venue.balances()
        assert bal["BTC"] == Balance("BTC", D("0.005"), D("0"))

    async def test_cancelar_orden_inexistente(self):
        with pytest.raises(VenueError, match="dry-nk-9"):
            await make().cancel("dry-nk-9")

    async def test_cancelar_todas(self):
        venue = make()
        await venue.place_limit(req(cid="t-1"))
        await venue.place_limit(req("sell", cid="t-2"))
        await venue.cancel_all()
        assert await venue.open_orders() == []
        assert all(b.locked == 0 for b in (await venue.balances()).values())

    async def test_los_saldos_iniciales_no_se_comparten(self):
        initial = balances()
        venue = DryRunVenue(FakeFeed("nk"), initial, clock=lambda: 1)
        await venue.place_limit(req())
        assert initial["BTC"].locked == 0


@pytest.mark.spec("RF-13")
async def test_el_stream_privado_avisa_de_cada_cambio():
    venue = make()
    seen = []

    async def read():
        async for ev in venue.private_stream():
            seen.append(ev)

    task = asyncio.create_task(read())
    await asyncio.sleep(0)
    order = await venue.place_limit(req())
    await venue.cancel(order.order_id)
    await asyncio.sleep(0.01)
    task.cancel()
    assert [o.status for o in seen] == ["open", "cancelled"]


@pytest.mark.spec("RF-13", "RF-09")
class TestFillSimulado:
    """Solo para tests (T-18): el dry-run de la terminal no simula fills (D-13)."""

    async def test_compra_parcial_y_completa(self):
        venue = make()
        order = await venue.place_limit(req(qty="0.01", price="0.00730000"))
        partial = await venue.simulate_fill(order.order_id, D("0.004"))
        assert (partial.status, partial.filled) == ("partial", D("0.004"))
        bal = await venue.balances()
        assert bal["BTCB2"].available == D("0.504")
        full = await venue.simulate_fill(order.order_id, D("0.006"))
        assert (full.status, full.filled) == ("filled", D("0.01"))
        bal = await venue.balances()
        spent = D("0.01") * D("0.00730000") * D("1.002")
        assert bal["BTC"] == Balance("BTC", D("0.005") - spent, D("0"))
        assert bal["BTCB2"].available == D("0.51")
        assert await venue.open_orders() == []

    async def test_venta_cobra_btc_menos_fee(self):
        venue = make()
        order = await venue.place_limit(req("sell", qty="0.2"))
        await venue.simulate_fill(order.order_id, D("0.2"))
        bal = await venue.balances()
        assert bal["BTCB2"] == Balance("BTCB2", D("0.3"), D("0"))
        assert bal["BTC"].available == D("0.005") + D("0.2") * D("0.00730000") * D("0.998")

    async def test_avisa_de_la_orden_y_del_fill(self):
        venue = make()
        seen = []

        async def read():
            async for ev in venue.private_stream():
                seen.append(ev)

        task = asyncio.create_task(read())
        await asyncio.sleep(0)
        order = await venue.place_limit(req())
        await venue.simulate_fill(order.order_id, D("0.01"))
        await asyncio.sleep(0.01)
        task.cancel()
        kinds = [type(e).__name__ for e in seen]
        assert kinds == ["Order", "Order", "Fill"]
        fill = seen[2]
        assert (fill.order_id, fill.qty, fill.fee) == (
            "dry-nk-1",
            D("0.01"),
            D("0.01") * D("0.00730000") * D("0.002"),
        )

    async def test_no_se_llena_mas_de_lo_pendiente(self):
        venue = make()
        order = await venue.place_limit(req())
        with pytest.raises(VenueError, match="queda"):
            await venue.simulate_fill(order.order_id, D("0.02"))


class RealAccount(FakeFeed):
    """Venue con saldos 'reales' que el test puede cambiar o hacer fallar."""

    def __init__(self):
        super().__init__("nk")
        self.real = {
            "BTC": Balance("BTC", D("0.003"), D("0.001")),
            "BTCB2": Balance("BTCB2", D("0.2")),
        }
        self.fail = None

    async def balances(self):
        if self.fail:
            raise self.fail
        return dict(self.real)


@pytest.mark.spec("RF-13", "RF-10")
class TestSaldosReales:
    """D-16: sin saldos fijos, el dry-run parte de los saldos reales de la cuenta."""

    async def test_sin_ordenes_son_los_reales(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        assert await venue.balances() == inner.real

    async def test_una_orden_simulada_bloquea_sobre_lo_real(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        await venue.place_limit(req(qty="0.01", price="0.00730000"))
        lock = D("0.01") * D("0.00730000") * D("1.002")
        bal = await venue.balances()
        assert bal["BTC"] == Balance("BTC", D("0.003") - lock, D("0.001") + lock)
        assert inner.real["BTC"] == Balance("BTC", D("0.003"), D("0.001"))  # lo real no se toca

    async def test_los_cambios_reales_se_ven(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        await venue.place_limit(req("sell", qty="0.1"))
        inner.real["BTCB2"] = Balance("BTCB2", D("0.7"))  # p. ej. un depósito
        bal = await venue.balances()
        assert bal["BTCB2"] == Balance("BTCB2", D("0.6"), D("0.1"))

    async def test_el_saldo_real_limita_las_ordenes_simuladas(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        with pytest.raises(VenueError, match="saldo insuficiente"):
            await venue.place_limit(req("sell", qty="0.3"))

    async def test_cancelar_devuelve_a_lo_real(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, clock=lambda: 1)
        order = await venue.place_limit(req())
        await venue.cancel(order.order_id)
        assert await venue.balances() == inner.real

    async def test_un_fallo_al_leer_se_propaga(self):
        inner = RealAccount()
        inner.fail = VenueError("NonKYC: HTTP 429")
        venue = DryRunVenue(inner, clock=lambda: 1)
        with pytest.raises(VenueError, match="429"):
            await venue.balances()


@pytest.mark.spec("RNF-11")
class TestSinEsperas:
    """D-18: enviar en dry-run no debe hacer peticiones REST."""

    async def test_usa_el_spec_del_hub(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, spec=lambda: spec("nk"), clock=lambda: 1)
        await venue.place_limit(req())
        assert "market" not in inner.calls

    async def test_sin_spec_del_hub_lo_pide(self):
        inner = RealAccount()
        venue = DryRunVenue(inner, spec=lambda: None, clock=lambda: 1)
        await venue.place_limit(req())
        assert inner.calls == ["market"]

    async def test_reutiliza_la_ultima_lectura_de_saldos(self):
        now = {"t": 1_000}
        inner = RealAccount()
        reads = []
        real_balances = inner.balances

        async def counted():
            reads.append(now["t"])
            return await real_balances()

        inner.balances = counted
        venue = DryRunVenue(inner, spec=lambda: spec("nk"), clock=lambda: now["t"])
        await venue.balances()  # lo que hace AccountService cada ≤ 10 s
        now["t"] += 29_000
        await venue.place_limit(req())
        assert reads == [1_000]
        now["t"] += 2_000  # 31 s desde la lectura: se vuelve a leer
        await venue.place_limit(req(cid="t-2"))
        assert reads == [1_000, 32_000]
