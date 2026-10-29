"""market_stream de Neoxa contra un servidor Socket.IO local que reproduce frames
reales (tests/fixtures/neoxa/ws_frames.json) y un REST simulado con respx para
el respaldo. Los tiempos se acortan para que el test dure milisegundos."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx
from websockets.asyncio.server import serve

from xbt_terminal.adapters.neoxa import REST_URL, NeoxaVenue
from xbt_terminal.core.models import BookSnapshot, ConnState, D, Level, Trade

FIXTURES = Path(__file__).parent.parent / "fixtures" / "neoxa"
FRAMES = json.loads((FIXTURES / "ws_frames.json").read_text(encoding="utf-8"))
API = REST_URL + "/exchange"
OPEN_FAST = '0{"sid":"s","upgrades":[],"pingInterval":200,"pingTimeout":200,"maxPayload":1000000}'
# Formato del trade en vivo según el cliente web de Neoxa (tradeId/time, no trade_id/executed_at)
LIVE_TRADE = (
    '42/exchange,["exchange:trade",{"pair":"BTCB2_BTC","tradeId":"TRD-LIVE-1","side":"buy",'
    '"quantity":0.5,"price":0.00722875,"total":0.003614375,"time":"2026-10-20T14:30:00.000Z"}]'
)
LAST_TRADE = LIVE_TRADE.replace("TRD-LIVE-1", "TRD-LIVE-2").replace(
    '"quantity":0.5', '"quantity":0.7'
)
OTHER_PAIR = '42/exchange,["exchange:orderbook",{"pair":"NEOX_BTC","bids":[],"asks":[]}]'
POOL = '42/exchange,["exchange:pool",{"pair":"BTCB2_BTC","reserveBase":25,"reserveQuote":0.18}]'


class FreeLimiter:
    async def acquire(self, kind: str) -> None:
        pass


class FakeNeoxa:
    """Cada conexión ejecuta el guion siguiente de `scripts`."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.received: list[list[str]] = []

    async def handler(self, ws):
        script = self.scripts.pop(0) if self.scripts else "silent"
        got: list[str] = []
        self.received.append(got)
        await ws.send(OPEN_FAST)
        if script == "silent":
            await ws.wait_closed()
            return
        got.append(await ws.recv())  # 40/exchange,
        await ws.send(FRAMES["connect"])
        got.append(await ws.recv())  # subscribe
        if script == "error":
            await ws.send('42/exchange,["exchange:error",{"message":"Invalid trading pair"}]')
            await ws.wait_closed()
            return
        for frame in (FRAMES["orderbook"], OTHER_PAIR, POOL, FRAMES["trades"], LIVE_TRADE):
            await ws.send(frame)
        await ws.send("2")
        got.append(await ws.recv())  # pong
        if script == "drop":
            return  # cierra la conexión
        await ws.send(LAST_TRADE)  # marca el final del guion
        await ws.wait_closed()


@pytest.fixture
def rest():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{API}/orderbook/BTCB2_BTC").respond(
            text=(FIXTURES / "orderbook.json").read_text(encoding="utf-8")
        )
        mock.get(f"{API}/trades/BTCB2_BTC").respond(
            text=(FIXTURES / "trades.json").read_text(encoding="utf-8")
        )
        yield mock


async def collect(server: FakeNeoxa, until, timeout: float = 3.0, on_event=None):
    """Eventos del stream hasta que `until(events)` sea cierto. `on_event(venue,
    events)`, si se da, se espera tras cada evento."""
    async with serve(server.handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as http:
            venue = NeoxaVenue(
                "BTCB2_BTC",
                FreeLimiter(),
                http=http,
                ws_url=f"ws://127.0.0.1:{port}/socket.io/?EIO=4&transport=websocket",
                fallback_every_s=0.01,
                backoff_base_s=0.01,
                backoff_max_s=0.05,
                clock=lambda: 1_792_506_600_000,
            )
            events = []
            stream = venue.market_stream()
            try:
                async with asyncio.timeout(timeout):
                    async for ev in stream:
                        events.append(ev)
                        if on_event is not None:
                            await on_event(venue, events)
                        if until(events):
                            return events
            finally:
                await stream.aclose()


def got_last(events) -> bool:
    return any(isinstance(e, Trade) and e.qty == D("0.7") for e in events)


def states(events):
    return [(e.source, e.status) for e in events if isinstance(e, ConnState)]


@pytest.mark.spec("RF-01")
class TestWebSocket:
    async def test_suscripcion_book_y_trades(self, rest):
        server = FakeNeoxa("stay")
        events = await collect(server, got_last)

        assert server.received[0][:2] == [
            "40/exchange,",
            '42/exchange,["subscribe",{"pair":"BTCB2_BTC"}]',
        ]
        assert isinstance(events[0], ConnState)
        assert (events[0].venue, events[0].channel, states(events)) == (
            "nx",
            "market",
            [("ws", "up")],
        )

        books = [e for e in events if isinstance(e, BookSnapshot)]
        assert len(books) == 1  # el de NEOX_BTC se ignora
        assert books[0].ts == 1_792_506_600_000 and books[0].seq is None
        assert len(books[0].bids) == 50 and len(books[0].asks) == 50
        assert books[0].bids[0] == Level(D("0.00713454"), D("0.12668383"), amm=True)

        trades = [e for e in events if isinstance(e, Trade)]
        assert [t.ts for t in trades[:20]] == sorted(t.ts for t in trades[:20])
        assert trades[-2] == Trade("nx", D("0.00722875"), D("0.5"), "buy", 1792506600000)
        assert len(trades) == 22

    async def test_responde_al_ping(self, rest):
        server = FakeNeoxa("drop")
        await collect(server, lambda evs: ("ws", "reconnecting") in states(evs))
        assert server.received[0][2] == "3"

    async def test_lote_inicial_no_repite_trades_ya_vistos(self, rest):
        """Tras caer y volver, el lote inicial del WS ya se entregó (WS o REST)."""
        server = FakeNeoxa("drop", "stay")
        events = await collect(server, got_last)
        assert states(events).count(("ws", "up")) == 2
        trades = [e for e in events if isinstance(e, Trade)]
        assert len(trades) == len(set(trades)) == 22  # 20 del lote + 2 en vivo, sin repetir


@pytest.mark.spec("RF-01", "RF-14")
class TestReconexion:
    """Reglas del supervisor (D-11) con el protocolo real de Neoxa."""

    async def test_una_caida_reconecta_por_ws(self, rest):
        server = FakeNeoxa("drop", "stay")
        events = await collect(server, lambda evs: states(evs).count(("ws", "up")) == 2)
        assert states(events) == [("ws", "up"), ("ws", "reconnecting"), ("ws", "up")]
        assert not rest.calls  # un fallo suelto no activa el REST

    async def test_tres_fallos_polling_y_vuelta_al_ws(self, rest):
        server = FakeNeoxa("error", "error", "error", "stay")
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
        assert rest_books and len(rest_books[0].bids) == 20  # el book REST del fixture

    async def test_rest_caido_se_reporta(self):
        server = FakeNeoxa("error", "error", "error", "stay")
        with respx.mock() as mock:
            mock.get(url__startswith=API).mock(side_effect=httpx.ConnectError("sin red"))
            events = await collect(server, lambda evs: ("ws", "up") in states(evs))
        assert ("rest", "down") in states(events)
        assert ("rest", "up") not in states(events)

    async def test_ws_mudo_cuenta_como_caida(self, rest):
        """Sin frames durante pingInterval + pingTimeout el WS se da por muerto."""
        server = FakeNeoxa("silent", "stay")
        events = await collect(server, lambda evs: ("ws", "up") in states(evs))
        assert states(events) == [("ws", "reconnecting"), ("ws", "up")]

    async def test_error_del_venue_antes_de_datos(self, rest):
        """El error llega tras suscribirse y antes del book: es un fallo, sin up."""
        server = FakeNeoxa("error", "stay")
        events = await collect(server, lambda evs: ("ws", "up") in states(evs))
        assert states(events) == [("ws", "reconnecting"), ("ws", "up")]


@pytest.mark.spec("RNF-24")
async def test_resync_reconecta_y_llega_otro_book(rest):
    """D-76: cada mensaje de Neoxa es el book entero, así que basta reconectar."""
    server = FakeNeoxa("stay", "stay")
    asked = []

    async def resync_after_first_book(venue, events):
        if not asked and any(isinstance(e, BookSnapshot) for e in events):
            asked.append(True)
            await venue.resync()

    events = await collect(
        server,
        lambda evs: len([e for e in evs if isinstance(e, BookSnapshot)]) == 2,
        on_event=resync_after_first_book,
    )
    assert len(server.received) == 2
    assert states(events)[0] == ("ws", "up") and states(events)[-1] == ("ws", "up")
