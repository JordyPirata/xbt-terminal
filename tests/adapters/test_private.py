"""Seguimiento común de órdenes y fills propios (D-14): diff entre refrescos y
bucle de polling con su estado de conexión privado."""

import asyncio
from contextlib import aclosing
from dataclasses import replace

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.adapters.private import (
    OrderTracker,
    PrivateSource,
    fan_in,
    poll_private,
    refresh,
)
from xbt_terminal.core.models import ConnState, D, FeeUpdate, Fill, Order, PastFill

T0 = 1_792_497_600_000


def order(oid="o-1", filled="0", status="open", cid=None, qty="0.01"):
    return Order("nx", oid, cid, "buy", D("0.0072"), D(qty), D(filled), status, T0)


def fill(tid="f-1", oid="o-1", qty="0.004", ts=T0 + 1):
    return Fill("nx", tid, oid, "buy", D("0.0072"), D(qty), D("0"), "BTC", ts)


@pytest.mark.spec("RF-08", "RF-09")
class TestTracker:
    def test_el_primer_refresco_da_las_ordenes_y_los_fills_viejos_como_pasados(self):
        """D-68: los trades que ya estaban no son fills nuevos, pero la cuenta los
        compara con el journal."""
        t = OrderTracker()
        events, gone = t.update([order("a"), order("b")], [fill("f-2", ts=T0 + 2), fill("f-1")])
        past = [PastFill(fill("f-1")), PastFill(fill("f-2", ts=T0 + 2))]  # por hora
        assert events == [order("a"), order("b"), *past]
        assert gone == [] and t.open == [order("a"), order("b")]

    def test_los_pasados_solo_en_el_primer_refresco(self):
        t = OrderTracker()
        t.update([], [fill("f-1")])
        assert t.update([], [fill("f-1")])[0] == []
        assert t.update([], [fill("f-1"), fill("f-2")])[0] == [fill("f-2")]

    def test_solo_avisa_de_lo_que_cambia(self):
        t = OrderTracker()
        t.update([order("a"), order("b")], [])
        events, _ = t.update([order("a"), order("b", filled="0.004", status="partial")], [])
        assert events == [order("b", filled="0.004", status="partial")]

    def test_fill_nuevo_una_sola_vez_y_en_orden(self):
        t = OrderTracker()
        t.update([], [fill("f-1")])
        new = [fill("f-3", ts=T0 + 3), fill("f-2", ts=T0 + 2), fill("f-1")]
        events, _ = t.update([], new)
        assert events == [fill("f-2", ts=T0 + 2), fill("f-3", ts=T0 + 3)]
        assert t.update([], new)[0] == []

    def test_orden_que_desaparece_se_consulta_hasta_resolverla(self):
        t = OrderTracker()
        t.update([order("a")], [])
        assert t.update([], [])[1] == ["a"]
        assert t.update([], [])[1] == ["a"]  # sigue pendiente mientras no se resuelva
        final = order("a", filled="0.01", status="filled")
        assert t.resolve(final) == [final]
        assert t.update([], [])[1] == [] and t.open == []

    def test_resolver_algo_que_no_se_sigue_no_avisa(self):
        assert OrderTracker().resolve(order("x", status="cancelled")) == []

    def test_recuerda_el_client_id_de_lo_que_envio_la_terminal(self):
        """Neoxa no guarda client_id: el de place_limit se mantiene en los refrescos."""
        t = OrderTracker()
        t.remember(order("a", cid="t-abc123"))
        events, _ = t.update([order("a")], [])
        assert events == [order("a", cid="t-abc123")]
        assert t.resolve(order("a", status="cancelled"))[0].client_id == "t-abc123"

    def test_lo_enviado_se_sigue_aunque_no_se_vea_activo(self):
        """Visto en real (2026-10-22 04:07, D-41): una venta en NonKYC se llenó en
        ~1 s, antes del siguiente polling, y su cierre no llegó nunca."""
        t = OrderTracker()
        t.update([], [])
        t.remember(order("a", cid="t-7bbce5"))
        assert t.open == [order("a", cid="t-7bbce5")]
        assert t.update([], [])[1] == ["a"]  # ya no está activa: se consulta
        final = order("a", filled="0.01", status="filled")
        assert t.resolve(final) == [order("a", filled="0.01", status="filled", cid="t-7bbce5")]

    def test_lo_enviado_ya_terminado_no_se_sigue(self):
        """Neoxa contesta "filled" al crear: no hay nada que consultar después."""
        t = OrderTracker()
        t.remember(order("a", filled="0.01", status="filled", cid="t-1"))
        assert t.open == [] and t.update([], [])[1] == []

    def test_lo_enviado_que_sigue_activo_no_se_da_por_desaparecido(self):
        t = OrderTracker()
        t.remember(order("a", cid="t-1"))
        events, gone = t.update([order("a")], [])
        assert gone == [] and t.open == [order("a", cid="t-1")]
        assert events == [order("a", cid="t-1")]  # la primera vez se avisa igual


class FakeSource:
    def __init__(self):
        self.active: list[Order] = []
        self.fills: list[Fill] = []
        self.final: dict[str, Order] = {}
        self.fail: Exception | None = None
        self.calls = 0

    async def fetch_active(self):
        self.calls += 1
        if self.fail:
            raise self.fail
        return list(self.active)

    async def fetch_fills(self):
        return list(self.fills)

    async def fetch_final(self, oid):
        return self.final.get(oid)

    def hooks(self):
        return PrivateSource(self.fetch_active, self.fetch_fills, self.fetch_final)


def runner(src, wake, every=0.01, source="rest"):
    def start(emit):
        return poll_private(
            "nx",
            OrderTracker(),
            src.hooks(),
            emit,
            every_s=lambda: every,
            source=lambda: source,
            wake=wake,
            clock=lambda: T0,
        )

    return start


async def take(stream, n, timeout=2.0):
    out = []
    async with asyncio.timeout(timeout):
        async for ev in stream:
            out.append(ev)
            if len(out) == n:
                return out
    return out


@pytest.mark.spec("RF-09", "RF-14")
class TestPolling:
    async def test_estado_up_y_ordenes(self):
        src = FakeSource()
        src.active = [order("a")]
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            state, o = await take(stream, 2)
        assert isinstance(state, ConnState)
        assert (state.channel, state.source, state.status) == ("private", "rest", "up")
        assert o == order("a")

    async def test_un_fallo_de_red_marca_caido_y_se_recupera(self):
        src = FakeSource()
        src.fail = httpx.ConnectError("sin red")
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            down = (await take(stream, 1))[0]
            src.fail = None
            up = (await take(stream, 1))[0]
        assert (down.status, up.status) == ("down", "up")

    async def test_error_del_venue_tambien_es_caido(self):
        src = FakeSource()
        src.fail = VenueError("401 Authorisation failed")
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            assert (await take(stream, 1))[0].status == "down"

    async def test_orden_terminada_se_resuelve(self):
        src = FakeSource()
        src.active = [order("a")]
        async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
            await take(stream, 2)
            src.active = []
            src.final["a"] = order("a", filled="0.01", status="filled")
            assert await take(stream, 1) == [order("a", filled="0.01", status="filled")]

    async def test_despertar_refresca_sin_esperar_la_cadencia(self):
        src = FakeSource()
        wake = asyncio.Event()
        async with aclosing(fan_in(runner(src, wake, every=60))) as stream:
            await take(stream, 1)
            src.active = [order("a")]
            wake.set()
            assert await take(stream, 1, timeout=1.0) == [order("a")]

    async def test_cambio_de_fuente_reemite_el_estado(self):
        src = FakeSource()
        wake = asyncio.Event()
        where = {"source": "rest"}

        def start(emit):
            return poll_private(
                "nk",
                OrderTracker(),
                src.hooks(),
                emit,
                every_s=lambda: 60,
                source=lambda: where["source"],
                wake=wake,
                clock=lambda: T0,
            )

        async with aclosing(fan_in(start)) as stream:
            assert (await take(stream, 1))[0].source == "rest"
            where["source"] = "ws"
            wake.set()
            assert (await take(stream, 1))[0].source == "ws"

    async def test_un_bug_no_se_traga(self):
        src = FakeSource()
        src.fail = ZeroDivisionError("bug")
        with pytest.raises(ZeroDivisionError):
            async with aclosing(fan_in(runner(src, asyncio.Event()))) as stream:
                await take(stream, 5)


@pytest.mark.spec("RF-09")
async def test_fan_in_mezcla_varios_trabajadores_y_los_para_al_cerrar():
    stopped = []

    def worker(name):
        async def run(emit):
            try:
                for i in range(3):
                    emit((name, i))
                    await asyncio.sleep(0)
                await asyncio.Event().wait()
            finally:
                stopped.append(name)

        return run

    async with aclosing(fan_in(worker("a"), worker("b"))) as stream:
        got = await take(stream, 6)
    assert sorted(got) == [(n, i) for n in "ab" for i in range(3)]
    await asyncio.sleep(0)
    assert sorted(stopped) == ["a", "b"]


@pytest.mark.spec("RF-07", "RF-09")
async def test_orden_llena_antes_del_primer_polling_sale_como_llena():
    """De punta a punta en el refresco: el venue ya no la lista como activa, y
    `getorder` dice que se llenó; sale un `Order` lleno con el client_id."""
    t = OrderTracker()
    filled = order("a", filled="0.01", status="filled")

    async def active():
        return []

    async def fills():
        return [fill("f-1", oid="a", qty="0.01")]

    async def final(oid):
        return filled if oid == "a" else None

    src = PrivateSource(active, fills, final)
    await refresh(t, src)  # primer refresco: siembra
    t.remember(order("a", cid="t-7bbce5"))
    events = await refresh(t, src)
    assert order("a", filled="0.01", status="filled", cid="t-7bbce5") in events
    assert t.open == []


@pytest.mark.spec("RF-11", "RF-25")
class TestFeeCorregida:
    """NonKYC rehace la fee de un trade ya publicado (BTC → NKYC, D-64)."""

    def test_la_misma_fee_no_avisa(self):
        t = OrderTracker()
        t.update([], [])
        t.update([], [fill("f-1")])
        assert t.update([], [fill("f-1")])[0] == []

    def test_otra_fee_avisa_una_vez_sin_otro_fill(self):
        t = OrderTracker()
        t.update([], [])
        assert t.update([], [fill("f-1")])[0] == [fill("f-1")]
        fixed = replace(fill("f-1"), fee=D("0.0000893166"), fee_asset="NKYC")
        events, _ = t.update([], [fixed])
        assert events == [FeeUpdate("nx", "f-1", "o-1", D("0.0000893166"), "NKYC", T0 + 1)]
        assert t.update([], [fixed])[0] == []

    def test_tambien_si_el_trade_era_historia(self):
        t = OrderTracker()
        t.update([], [fill("f-viejo")])
        fixed = replace(fill("f-viejo"), fee=D("0.00001"))
        (event,), _ = t.update([], [fixed])
        assert isinstance(event, FeeUpdate) and event.fee == D("0.00001")
