"""Límites de Neoxa con el 20 % de margen: 480/60 s general, 8 órdenes y 16
cancelaciones por 10 s. Reloj falso: el test avanza el tiempo y llama a
dispatch(), que es lo que en producción hace el temporizador del limitador.
"""

import asyncio

import pytest

from xbt_terminal.core.ratelimit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def neoxa(clock):
    lim = RateLimiter(general=(480, 60), order=(8, 10), cancel=(16, 10), clock=clock)
    yield lim
    lim.close()


async def burst(lim: RateLimiter, kind: str, n: int) -> None:
    for _ in range(n):
        await asyncio.wait_for(lim.acquire(kind), 0.1)


async def pending(lim: RateLimiter, kind: str) -> asyncio.Task:
    task = asyncio.create_task(lim.acquire(kind))
    await asyncio.sleep(0)
    return task


async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.spec("RNF-03")
class TestVentanas:
    async def test_la_481_espera_un_minuto(self, neoxa, clock):
        await burst(neoxa, "market", 480)
        task = await pending(neoxa, "market")
        clock.t += 59.9
        neoxa.dispatch()
        await settle()
        assert not task.done()
        clock.t += 0.2
        neoxa.dispatch()
        await settle()
        assert task.done()

    async def test_ventana_deslizante(self, neoxa, clock):
        await burst(neoxa, "market", 240)
        clock.t += 30
        await burst(neoxa, "market", 240)
        task = await pending(neoxa, "market")
        clock.t += 30.1  # caducan solo las 240 primeras
        neoxa.dispatch()
        await settle()
        assert task.done()
        await burst(neoxa, "market", 239)
        assert not (await pending(neoxa, "market")).done()

    async def test_la_novena_orden_espera(self, neoxa, clock):
        await burst(neoxa, "order", 8)
        task = await pending(neoxa, "order")
        clock.t += 10.1
        neoxa.dispatch()
        await settle()
        assert task.done()

    async def test_cancelaciones_16_por_10s(self, neoxa):
        await burst(neoxa, "cancel", 16)
        assert not (await pending(neoxa, "cancel")).done()

    async def test_ordenes_y_polling_comparten_presupuesto(self, neoxa):
        await burst(neoxa, "order", 8)
        await burst(neoxa, "private", 472)
        assert not (await pending(neoxa, "market")).done()

    async def test_orden_bloqueada_no_frena_el_polling(self, neoxa):
        await burst(neoxa, "order", 8)
        order = await pending(neoxa, "order")
        await burst(neoxa, "market", 1)  # pasa aunque haya una orden en cola
        assert not order.done()

    async def test_venue_sin_ventanas_de_ordenes(self, clock):
        lim = RateLimiter(general=(240, 60), clock=clock)
        await burst(lim, "order", 240)
        assert not (await pending(lim, "cancel")).done()
        lim.close()


@pytest.mark.spec("RNF-03")
class TestPrioridad:
    async def test_cancelacion_adelanta_al_polling(self, neoxa, clock):
        await burst(neoxa, "market", 2)
        clock.t += 1
        await burst(neoxa, "market", 478)
        market = await pending(neoxa, "market")
        private = await pending(neoxa, "private")
        order = await pending(neoxa, "order")
        cancel = await pending(neoxa, "cancel")

        clock.t += 59.5  # caducan solo las 2 primeras: 2 huecos
        neoxa.dispatch()
        await settle()
        assert cancel.done() and order.done()
        assert not private.done() and not market.done()

        clock.t += 1  # caduca el resto
        neoxa.dispatch()
        await settle()
        assert private.done() and market.done()

    async def test_fifo_dentro_de_la_misma_prioridad(self, neoxa, clock):
        await burst(neoxa, "market", 480)
        first = await pending(neoxa, "market")
        second = await pending(neoxa, "market")
        neoxa._windows["general"].stamps.popleft()  # libera exactamente un hueco
        neoxa.dispatch()
        await settle()
        assert first.done() and not second.done()

    async def test_espera_cancelada_no_consume_hueco(self, neoxa, clock):
        await burst(neoxa, "market", 480)
        gone = await pending(neoxa, "cancel")
        gone.cancel()
        later = await pending(neoxa, "market")
        neoxa._windows["general"].stamps.popleft()
        neoxa.dispatch()
        await settle()
        assert later.done()


async def test_tipo_desconocido(neoxa):
    with pytest.raises(ValueError):
        await neoxa.acquire("withdraw")


async def test_temporizador_real_despierta_la_cola():
    lim = RateLimiter(general=(2, 0.05))
    await burst(lim, "market", 2)
    await asyncio.wait_for(lim.acquire("market"), 1)  # sin dispatch() manual
    lim.close()
