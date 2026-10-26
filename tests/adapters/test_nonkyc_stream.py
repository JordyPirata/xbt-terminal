"""market_stream de NonKYC contra un servidor JSON-RPC local que reproduce frames
reales (tests/fixtures/nonkyc/ws_frames.json) y un REST simulado con respx.

Los 17 `updateOrderbook` grabados encadenan con el snapshot (91658) y llegaron
desordenados (91663 antes que 91662, 91666 antes que 91665, 91673 antes que 91672): se reproducen en
ese mismo orden."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx
from websockets.asyncio.server import serve

from btcb2_terminal.adapters.nonkyc import REST_URL, NonKYCVenue
from btcb2_terminal.adapters.streaming import iso_ms
from btcb2_terminal.core.book import L2Book
from btcb2_terminal.core.models import BookDelta, BookSnapshot, ConnState, D, Level, Trade

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nonkyc"
FRAMES = json.loads((FIXTURES / "ws_frames.json").read_text(encoding="utf-8"))
UPDATES: list[str] = FRAMES["updateOrderbook"]
SEQ = 91658  # sequence del snapshot grabado
LAST_SEQ = SEQ + len(UPDATES)


def update_book(seq: int, bids=(), asks=()) -> str:
    """Mismo formato que los updateOrderbook reales."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "updateOrderbook",
            "params": {
                "asks": [{"price": p, "quantity": q} for p, q in asks],
                "bids": [{"price": p, "quantity": q} for p, q in bids],
                "symbol": "BTCB2/BTC",
                "timestamp": "2026-10-20T15:30:00.000Z",
                "sequence": seq,
            },
        }
    )


LAST_TRADE = (
    FRAMES["updateTrades"]
    .replace("6ad78386d8baa7ac8047f1ab", "fin-del-guion")
    .replace('"quantity":"0.5095"', '"quantity":"0.7777"')
)
OTHER_SYMBOL = update_book(SEQ + 1).replace("BTCB2/BTC", "XMR/BTC")


class FakeNonKYC:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.received: list[list[dict]] = []

    async def handler(self, ws):
        script = self.scripts.pop(0) if self.scripts else "stay"
        got: list[dict] = []
        self.received.append(got)

        async def expect(method: str):
            msg = json.loads(await ws.recv())
            got.append(msg)
            assert msg["method"] == method, msg

        await expect("subscribeOrderbook")
        await expect("subscribeTrades")
        if script == "error":
            await ws.send(FRAMES["errorSymbol"])  # y deja la conexión abierta
            await ws.wait_closed()
            return
        if script == "silent":
            await ws.wait_closed()
            return
        await ws.send(FRAMES["snapshotOrderbook"])
        await ws.send(FRAMES["snapshotTrades"])
        await ws.send(OTHER_SYMBOL)
        if script == "gap":
            await ws.send(update_book(SEQ + 2, bids=[("0.0071", "1")]))  # falta SEQ + 1
            await expect("subscribeOrderbook")  # el adaptador pide un snapshot nuevo
            await ws.send(FRAMES["snapshotOrderbook"])
        else:
            for frame in UPDATES:
                await ws.send(frame)
        if script == "dup":
            await ws.send(UPDATES[0])  # repetido: ya aplicado
            await asyncio.sleep(0.2)  # más que reorder_wait_s
        await ws.send(FRAMES["updateTrades"])
        if script == "drop":
            return
        await ws.send(LAST_TRADE)
        await ws.wait_closed()


class FreeLimiter:
    async def acquire(self, kind: str) -> None:
        pass


@pytest.fixture
def rest():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{REST_URL}/market/orderbook").respond(
            text=(FIXTURES / "orderbook.json").read_text(encoding="utf-8")
        )
        mock.get(f"{REST_URL}/market/trades").respond(
            text=(FIXTURES / "trades.json").read_text(encoding="utf-8")
        )
        yield mock


def make_venue(http, port: int) -> NonKYCVenue:
    return NonKYCVenue(
        "BTCB2/BTC",
        FreeLimiter(),
        maker_fee=D("0.002"),
        taker_fee=D("0.002"),
        http=http,
        ws_url=f"ws://127.0.0.1:{port}",
        fallback_every_s=0.01,
        backoff_base_s=0.01,
        backoff_max_s=0.05,
        reorder_wait_s=0.05,
        clock=lambda: 1_792_509_100_000,
    )


async def collect(server, until, timeout: float = 3.0):
    async with serve(server.handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as http:
            venue = make_venue(http, port)
            events = []
            stream = venue.market_stream()
            try:
                async with asyncio.timeout(timeout):
                    async for ev in stream:
                        events.append(ev)
                        if until(events):
                            return events
            finally:
                await stream.aclose()


def is_last(events) -> bool:
    """LAST_TRADE es lo último de cada guion."""
    return any(isinstance(e, Trade) and e.qty == D("0.7777") for e in events)


def states(events):
    return [(e.source, e.status) for e in events if isinstance(e, ConnState)]


def book_events(events):
    return [e for e in events if isinstance(e, BookSnapshot | BookDelta)]


def expected_book() -> L2Book:
    """El book aplicando los frames grabados ordenados por sequence, a mano."""
    book = L2Book("nk")
    snap = json.loads(FRAMES["snapshotOrderbook"])["params"]
    book.apply(
        BookSnapshot(
            "nk",
            tuple(Level(D(lv["price"]), D(lv["quantity"])) for lv in snap["bids"]),
            tuple(Level(D(lv["price"]), D(lv["quantity"])) for lv in snap["asks"]),
            ts=0,
            seq=SEQ,
        )
    )
    for p in sorted((json.loads(u)["params"] for u in UPDATES), key=lambda p: p["sequence"]):
        assert book.apply(
            BookDelta(
                "nk",
                tuple(Level(D(str(lv["price"])), D(str(lv["quantity"]))) for lv in p["bids"]),
                tuple(Level(D(str(lv["price"])), D(str(lv["quantity"]))) for lv in p["asks"]),
                ts=0,
                seq=p["sequence"],
            )
        )
    return book


@pytest.mark.spec("RF-01", "RF-02")
class TestWebSocket:
    async def test_suscripcion_y_snapshot(self, rest):
        server = FakeNonKYC("stay")
        events = await collect(server, is_last)

        assert server.received[0] == [
            {"method": "subscribeOrderbook", "params": {"symbol": "BTCB2/BTC"}, "id": 1},
            {"method": "subscribeTrades", "params": {"symbol": "BTCB2/BTC"}, "id": 2},
        ]
        assert isinstance(events[0], ConnState) and states(events) == [("ws", "up")]
        snap = events[1]
        assert isinstance(snap, BookSnapshot)
        assert (snap.venue, snap.seq) == ("nk", SEQ)
        assert snap.ts == json.loads(FRAMES["snapshotOrderbook"])["timestamp"]

    async def test_deltas_desordenados_se_reordenan(self, rest):
        server = FakeNonKYC("stay")
        events = await collect(server, is_last)
        deltas = [e for e in events if isinstance(e, BookDelta)]
        assert [d.seq for d in deltas] == list(range(SEQ + 1, LAST_SEQ + 1))
        assert deltas[0].ts == iso_ms("2026-10-20T15:14:43.717Z")
        assert deltas[0].asks == (Level(D("0.00724756"), D("0")),)  # quantity: 0 numérico
        assert len(server.received[0]) == 2  # sin resuscripciones: no hubo hueco

    async def test_el_book_queda_igual_que_aplicando_en_orden(self, rest):
        server = FakeNonKYC("stay")
        events = await collect(server, is_last)
        book = L2Book("nk")
        assert all(book.apply(e) for e in book_events(events))
        want = expected_book()
        assert book.top(100) == want.top(100)
        assert book.seq == LAST_SEQ and book.mid is not None

    async def test_otro_simbolo_se_ignora(self, rest):
        events = await collect(FakeNonKYC("stay"), is_last)
        assert all(e.venue == "nk" for e in events)
        assert len([e for e in events if isinstance(e, BookDelta)]) == len(UPDATES)

    async def test_trades(self, rest):
        events = await collect(FakeNonKYC("stay"), is_last)
        trades = [e for e in events if isinstance(e, Trade)]
        assert len(trades) == 52  # 50 del snapshot + el real en vivo + el de fin
        assert [t.ts for t in trades[:50]] == sorted(t.ts for t in trades[:50])
        assert trades[50] == Trade("nk", D("0.00724378"), D("0.5095"), "sell", 1792508806091)

    async def test_delta_repetido_se_ignora(self, rest):
        server = FakeNonKYC("dup")
        events = await collect(server, is_last)
        deltas = [e for e in events if isinstance(e, BookDelta)]
        assert [d.seq for d in deltas] == list(range(SEQ + 1, LAST_SEQ + 1))
        assert len(server.received[0]) == 2  # no se tomó por hueco

    async def test_hueco_real_pide_snapshot_nuevo(self, rest):
        server = FakeNonKYC("gap")
        events = await collect(server, is_last)
        assert [m["method"] for m in server.received[0]] == [
            "subscribeOrderbook",
            "subscribeTrades",
            "subscribeOrderbook",
        ]
        book = L2Book("nk")
        valid = [book.apply(e) for e in book_events(events)]
        assert valid == [True, False, True]  # el hueco invalida, el snapshot repara
        assert book.valid and book.seq == SEQ

    async def test_lote_inicial_no_repite_trades(self, rest):
        server = FakeNonKYC("drop", "stay")
        events = await collect(
            server, lambda evs: states(evs).count(("ws", "up")) == 2 and is_last(evs)
        )
        trades = [e for e in events if isinstance(e, Trade)]
        assert len(trades) == len(set(trades))


@pytest.mark.spec("RF-01", "RF-14")
class TestReconexion:
    """Reglas del supervisor (D-11) con el protocolo real de NonKYC."""

    async def test_una_caida_reconecta_por_ws(self, rest):
        server = FakeNonKYC("drop", "stay")
        events = await collect(server, lambda evs: states(evs).count(("ws", "up")) == 2)
        assert states(events) == [("ws", "up"), ("ws", "reconnecting"), ("ws", "up")]
        assert not rest.calls

    async def test_tres_fallos_polling_y_vuelta_al_ws(self, rest):
        server = FakeNonKYC("error", "error", "error", "stay")
        events = await collect(server, lambda evs: ("ws", "up") in states(evs))
        assert states(events) == [
            ("ws", "reconnecting"),
            ("ws", "reconnecting"),
            ("ws", "reconnecting"),
            ("rest", "up"),
            ("ws", "up"),
        ]
        i = next(i for i, e in enumerate(events) if isinstance(e, ConnState) and e.source == "rest")
        rest_books = [e for e in events[i:] if isinstance(e, BookSnapshot)]
        assert rest_books[0].seq is None and len(rest_books[0].bids) == 20

    async def test_sin_snapshot_no_esta_up(self, rest):
        """Conectado pero sin datos: no se declara up hasta el primer snapshot."""
        server = FakeNonKYC("silent")
        with pytest.raises(TimeoutError):
            await collect(server, lambda evs: bool(evs), timeout=0.3)


@pytest.mark.spec("RF-01")
async def test_cerrar_el_stream_cierra_el_ws(rest):
    """Con más de 16 frames sin leer, el cierre limpio esperaría los 10 s por
    defecto de websockets: `WS_CLOSE_TIMEOUT_S` lo acota (salir de la terminal
    no puede tardar 10 s)."""
    server = FakeNonKYC("stay")
    async with serve(server.handler, "127.0.0.1", 0) as srv:
        async with httpx.AsyncClient() as http:
            stream = make_venue(http, srv.sockets[0].getsockname()[1]).market_stream()
            async for ev in stream:
                if isinstance(ev, BookSnapshot):
                    break
            await asyncio.sleep(0.1)  # que lleguen y se encolen los frames restantes
            await asyncio.wait_for(stream.aclose(), 1.5)
