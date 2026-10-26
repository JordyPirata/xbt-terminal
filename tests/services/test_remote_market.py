"""Mercado por el socket del daemon (T-62, RF-31, D-66): el cliente ve el
mismo book, velas, último trade y conexión que el hub del daemon, sin abrir
ninguna conexión a los venues."""

import asyncio

import pytest

from xbt_terminal.core.models import BookSnapshot, Candle, ConnState, D, Level, Trade
from xbt_terminal.services.control import ControlServer
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.remote import RemoteEngine

from .test_orders import NOW
from .test_uncertain import Rig

MIN = 60_000
T0 = NOW - NOW % MIN - 10 * MIN  # alineado al minuto


async def until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def candles(n=3, price="0.00725"):
    p = D(price)
    return [Candle(T0 + i * MIN, p, p, p, p, D("1")) for i in range(n)]


def trade(vid, price, ts, qty="0.5"):
    return Trade(vid, D(price), D(qty), "buy", ts)


def book(vid, bids, asks, ts=NOW, seq=None):
    lv = lambda rows: tuple(Level(D(p), D(q)) for p, q in rows)  # noqa: E731
    return BookSnapshot(vid, lv(bids), lv(asks), ts, seq)


class Daemon:
    def __init__(self, tmp_path, book_depth=50):
        self.s = Rig(tmp_path)
        self.hub = self.s.hub
        for st in self.hub.state.values():
            st.candles["1m"].load(candles())
            st.ready, st.loaded_at = True, T0
        self.engine = Engine(self.hub, mode="dry-run", orders=self.s.orders, journal=self.s.journal)
        self.path = tmp_path / "m.sock"
        self.server = ControlServer(
            self.engine, self.path, every_s=0.05, book_every_s=0.01, book_depth=book_depth
        )

    async def __aenter__(self):
        await self.server.start()
        return self

    async def __aexit__(self, *exc):
        await self.server.aclose()


@pytest.fixture
async def linked(tmp_path):
    async with Daemon(tmp_path) as daemon:
        remote = RemoteEngine(daemon.path, has_bot=False, retry_s=0.02)
        task = asyncio.create_task(remote.run())
        await until(lambda: remote.market.state["nk"].ready and remote.market.state["nx"].ready)
        yield daemon, remote
        task.cancel()
        await remote.aclose()


def same_book(a, b, n=50):
    return a.top(n) == b.top(n) and a.ts == b.ts


@pytest.mark.spec("RF-31", "CA-16")
class TestFotoAlConectar:
    async def test_spec_book_velas_y_conexion(self, linked):
        daemon, remote = linked
        for vid in ("nk", "nx"):
            mine, theirs = remote.market.state[vid], daemon.hub.state[vid]
            assert mine.spec == theirs.spec
            assert same_book(mine.book, theirs.book)
            assert mine.book.valid == theirs.book.valid
            for tf, builder in theirs.candles.items():
                assert mine.candles[tf].candles == builder.candles
        assert remote.market.state["nk"].book.mid == daemon.hub.state["nk"].book.mid

    async def test_un_venue_sin_cargar_no_esta_listo(self, tmp_path):
        async with Daemon(tmp_path) as d:
            d.hub.state["nx"].ready = False
            d.hub.state["nx"].error = "HTTP 503"
            remote = RemoteEngine(d.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.market.state["nk"].ready)
            assert not remote.market.state["nx"].ready
            assert remote.market.state["nx"].error == "HTTP 503"
            task.cancel()
            await remote.aclose()

    async def test_el_venue_que_carga_despues_manda_otra_foto(self, tmp_path):
        async with Daemon(tmp_path) as d:
            d.hub.state["nx"].ready = False
            remote = RemoteEngine(d.path, has_bot=False, retry_s=0.02)
            seen = []
            remote.market.subscribe(lambda vid, what: seen.append((vid, what)))
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.market.state["nk"].ready)
            d.hub.state["nx"].ready = True
            d.hub._notify("nx", "market")
            await until(lambda: remote.market.state["nx"].ready)
            assert ("nx", "market") in seen and ("nx", "candles") in seen
            task.cancel()
            await remote.aclose()


@pytest.mark.spec("RF-31", "RF-02", "RF-03", "RF-14")
class TestEventos:
    async def test_book_nuevo(self, linked):
        daemon, remote = linked
        seen = []
        remote.market.subscribe(lambda vid, what: seen.append((vid, what)))
        daemon.hub.apply("nx", book("nx", [("0.0071", "2"), ("0.0070", "3")], [("0.0074", "1")]))
        await until(lambda: remote.market.state["nx"].book.mid == daemon.hub.state["nx"].book.mid)
        assert same_book(remote.market.state["nx"].book, daemon.hub.state["nx"].book)
        assert ("nx", "book") in seen

    async def test_el_book_viaja_con_la_profundidad_acotada(self, tmp_path):
        async with Daemon(tmp_path, book_depth=2) as d:
            bids = [(f"0.00{700 - i}", "1") for i in range(5)]
            d.hub.apply("nx", book("nx", bids, [("0.0074", "1")]))
            remote = RemoteEngine(d.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.market.state["nx"].book.valid)
            assert remote.market.state["nx"].book.depth == (2, 1)
            assert remote.market.state["nx"].book.best_bid == D("0.00700")
            task.cancel()
            await remote.aclose()

    async def test_muchos_cambios_seguidos_llegan_agrupados_y_queda_el_ultimo(self, linked):
        daemon, remote = linked
        for i in range(30):
            daemon.hub.apply("nx", book("nx", [(f"0.00{690 + i}", "1")], [("0.0074", "1")]))
        await until(lambda: remote.market.state["nx"].book.best_bid == D("0.00719"))

    async def test_trade_mueve_velas_y_ultimo_trade(self, linked):
        daemon, remote = linked
        t = trade("nk", "0.0080", T0 + 2 * MIN + 1)
        daemon.hub.apply("nk", t)
        await until(lambda: remote.market.state["nk"].last_trade == t)
        theirs = daemon.hub.state["nk"].candles
        for tf, builder in remote.market.state["nk"].candles.items():
            assert builder.candles == theirs[tf].candles

    async def test_un_trade_que_el_hub_descarta_no_viaja(self, linked):
        daemon, remote = linked
        old = trade("nk", "0.0090", T0 - MIN)  # anterior al histórico
        daemon.hub.apply("nk", old)
        new = trade("nk", "0.0080", T0 + 2 * MIN + 1)
        daemon.hub.apply("nk", new)
        await until(lambda: remote.market.state["nk"].last_trade == new)
        assert remote.market.state["nk"].candles["1m"].candles == (
            daemon.hub.state["nk"].candles["1m"].candles
        )

    async def test_conexion(self, linked):
        daemon, remote = linked
        conn = ConnState("nk", "market", "rest", "reconnecting", None, NOW)
        daemon.hub.apply("nk", conn)
        await until(lambda: remote.market.state["nk"].conn == conn)
        assert remote.market.state["nk"].conn_at is not None


@pytest.mark.spec("RF-31")
class TestReconectar:
    async def test_al_volver_el_daemon_llega_otra_foto(self, tmp_path):
        async with Daemon(tmp_path) as d:
            remote = RemoteEngine(d.path, has_bot=False, retry_s=0.02)
            task = asyncio.create_task(remote.run())
            await until(lambda: remote.market.state["nk"].ready)
            await d.server.aclose()
            await until(lambda: not remote.connected)
            d.hub.apply("nk", book("nk", [("0.0060", "1")], [("0.0061", "1")]))
            await d.server.start()
            await until(lambda: remote.market.state["nk"].book.best_bid == D("0.0060"))
            task.cancel()
            await remote.aclose()

    async def test_las_velas_avanzan_solas_en_el_cliente(self, linked):
        _, remote = linked
        before = len(remote.market.state["nk"].candles["1m"].candles)
        remote.market.roll(T0 + 6 * MIN)
        assert len(remote.market.state["nk"].candles["1m"].candles) > before
