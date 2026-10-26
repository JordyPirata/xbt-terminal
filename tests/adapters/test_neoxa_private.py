"""REST privado de Neoxa (T-17, D-14) con claves falsas. Fixtures con la forma
de las respuestas reales grabadas el 2026-10-20 (IDs, usuario e importes
cambiados); place y cancel salen de los ejemplos de /api-docs, porque no se
pueden grabar sin enviar una orden real."""

import asyncio
import json
from contextlib import aclosing
from pathlib import Path

import httpx
import pytest
import respx

from xbt_terminal.adapters.base import ReadOnlyError, UncertainError, Venue, VenueError
from xbt_terminal.adapters.neoxa import REST_URL, NeoxaVenue
from xbt_terminal.config import Credentials
from xbt_terminal.core.models import Balance, ConnState, D, Fill, Order, OrderRequest
from xbt_terminal.core.secrets import Secret

FIXTURES = Path(__file__).parent.parent / "fixtures" / "neoxa"
API = REST_URL + "/exchange"
NOW = 1_792_506_300_000
KEY, SECRET = "nex_fakekey0000000000000000000000000000000000000000", "f" * 64
CREDS = Credentials(Secret(KEY), Secret(SECRET))


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class SpyLimiter:
    def __init__(self) -> None:
        self.kinds: list[str] = []

    async def acquire(self, kind: str) -> None:
        self.kinds.append(kind)


@pytest.fixture
def limiter():
    return SpyLimiter()


def make(http, limiter, creds=CREDS, every=3.0):
    return NeoxaVenue(
        "BTCB2_BTC", limiter, http=http, credentials=creds, private_every_s=every, clock=lambda: NOW
    )


@pytest.fixture
async def venue(limiter):
    async with httpx.AsyncClient() as http:
        yield make(http, limiter)


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{API}/balances").respond(text=fixture("balances.json"))
        mock.get(f"{API}/orders").respond(text=fixture("orders_active.json"))
        mock.get(f"{API}/orders/history").respond(text=fixture("orders_history.json"))
        mock.get(f"{API}/trades/my").respond(text=fixture("trades_my.json"))
        mock.post(f"{API}/order").respond(text=fixture("order_placed.json"))
        mock.delete(url__regex=rf"{API}/order/.+", name="cancel").respond(
            text=fixture("order_cancelled.json")
        )
        yield mock


def req(side="buy", qty="0.01", price="0.00725000"):
    return OrderRequest("nx", side, D(qty), D(price), "t-abc123")


@pytest.mark.spec("RNF-09", "RF-12")
class TestCredenciales:
    def test_cumple_venue(self, limiter):
        assert isinstance(NeoxaVenue("BTCB2_BTC", limiter, credentials=CREDS), Venue)

    async def test_sin_keys_no_se_llama_al_venue(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, creds=None)
            with pytest.raises(ReadOnlyError):
                await venue.balances()
            with pytest.raises(ReadOnlyError):
                await venue.place_limit(req())
        assert not api.calls

    async def test_cabeceras_planas(self, venue, api):
        await venue.balances()
        sent = api.calls.last.request
        assert sent.headers["X-API-Key"] == KEY and sent.headers["X-API-Secret"] == SECRET

    async def test_un_error_no_muestra_el_secreto(self, venue, api):
        api.get(f"{API}/balances").respond(401, json={"success": False, "error": "Invalid API key"})
        with pytest.raises(VenueError) as err:
            await venue.balances()
        assert "Invalid API key" in str(err.value)
        assert SECRET not in str(err.value) and KEY not in str(err.value)


@pytest.mark.spec("RF-05", "RNF-10")
async def test_balances_solo_btc_y_btcb2(venue, api, limiter):
    """`total` de Neoxa no incluye lo comprometido en órdenes (`in_orders`)."""
    bal = await venue.balances()
    assert bal == {
        "BTC": Balance("BTC", D("0.0021"), D("0.00150005")),
        "BTCB2": Balance("BTCB2", D("0.25"), D("0.1")),
    }
    assert limiter.kinds == ["private"]


@pytest.mark.spec("RF-08")
class TestOrdenes:
    async def test_abiertas(self, venue, api, limiter):
        orders = await venue.open_orders()
        assert orders == [
            Order(
                "nx", "ORD-TEST0002-BBBB0002", None, "sell", D("0.0075"), D("0.1"),
                D("0.000025"), "partial", 1_792_530_360_000,
            ),
            Order(
                "nx", "ORD-TEST0001-AAAA0001", None, "buy", D("0.0071"), D("0.2"),
                D("0"), "open", 1_792_530_334_660,
            ),
        ]  # fmt: skip
        params = api.calls.last.request.url.params
        assert (params["status"], params["pair"]) == ("active", "BTCB2_BTC")
        assert limiter.kinds == ["private"]

    async def test_enviar_limite_con_numeros_exactos(self, venue, api, limiter):
        order = await venue.place_limit(req())
        body = api.calls.last.request.content.decode()
        assert body == '{"pair":"BTCB2_BTC","side":"buy","quantity":0.01,"price":0.00725000}'
        assert order == Order(
            "nx", "ORD-TEST0005-EEEE0005", "t-abc123", "buy", D("0.00725"), D("0.01"), D("0"),
            "open", NOW,
        )  # fmt: skip
        assert limiter.kinds == ["order"]

    async def test_rechazo_del_venue(self, venue, api):
        api.post(f"{API}/order").respond(
            400,
            json={
                "success": False,
                "error": "Sell price is far below",
                "code": "PRICE_OUT_OF_BAND",
            },
        )
        with pytest.raises(VenueError, match="PRICE_OUT_OF_BAND"):
            await venue.place_limit(req())

    async def test_cancelar(self, venue, api, limiter):
        await venue.cancel("ORD-TEST0005-EEEE0005")
        sent = api.calls.last.request
        assert (sent.method, sent.url.path) == (
            "DELETE",
            "/api/exchange/order/ORD-TEST0005-EEEE0005",
        )
        assert limiter.kinds == ["cancel"]

    async def test_cancelar_todas_intenta_todas_aunque_falle_una(self, venue, api):
        def cancel(request):
            if request.url.path.endswith("ORD-TEST0002-BBBB0002"):
                return httpx.Response(400, json={"success": False, "error": "Order not found"})
            return httpx.Response(200, text=fixture("order_cancelled.json"))

        api["cancel"].side_effect = cancel
        with pytest.raises(VenueError, match="ORD-TEST0002-BBBB0002"):
            await venue.cancel_all()
        deleted = [c.request.url.path for c in api.calls if c.request.method == "DELETE"]
        assert deleted == [
            "/api/exchange/order/ORD-TEST0002-BBBB0002",
            "/api/exchange/order/ORD-TEST0001-AAAA0001",
        ]


@pytest.mark.spec("RF-09")
class TestFills:
    async def test_trades_propios_del_par(self, venue, api):
        fills = await venue.fetch_fills()
        assert [f.trade_id for f in fills] == [
            "TRD-TEST0001-00000001", "AMM-TEST0002-00000002", "TRD-TEST0003-00000003",
        ]  # fmt: skip
        assert fills[0] == Fill(
            "nx", "TRD-TEST0001-00000001", None, "buy", D("0.00725"), D("0.02683261"),
            D("3.9e-07"), "BTC", 1_792_531_543_116,
        )  # fmt: skip
        assert (fills[1].fee, fills[1].fee_asset) == (D("0"), None)  # AMM: sin fee

    async def test_estado_final_desde_el_historial(self, venue, api):
        final = await venue.fetch_final("ORD-TEST0001-AAAA0001")
        assert final is not None and (final.status, final.filled) == ("cancelled", D("0"))
        assert await venue.fetch_final("ORD-NO-EXISTE") is None

    async def test_el_historial_no_mezcla_otros_pares(self, venue, api):
        assert await venue.fetch_final("ORD-TEST0009-USDC0009") is None


@pytest.mark.spec("RF-09", "RNF-04", "RNF-03")
class TestStreamPrivado:
    async def test_estado_y_ordenes_abiertas(self, venue, api):
        async with aclosing(venue.private_stream()) as stream:
            events = [await anext(stream) for _ in range(3)]
        state = events[0]
        assert isinstance(state, ConnState)
        assert (state.channel, state.source, state.status) == ("private", "rest", "up")
        assert [o.order_id for o in events[1:]] == [
            "ORD-TEST0002-BBBB0002",
            "ORD-TEST0001-AAAA0001",
        ]

    async def test_respeta_cadencia_y_limitador(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, every=0.1)
            async with aclosing(venue.private_stream()) as stream:
                await anext(stream)
                await asyncio.sleep(0.35)
        polls = [c for c in api.calls if c.request.url.path == "/api/exchange/orders"]
        assert 3 <= len(polls) <= 5  # uno al empezar y uno cada 0.1 s
        assert set(limiter.kinds) == {"private"}

    async def test_fill_nuevo_entre_refrescos(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, every=0.05)
            async with aclosing(venue.private_stream()) as stream:
                for _ in range(3):
                    await anext(stream)
                trades = json.loads(fixture("trades_my.json"))
                new = dict(trades["trades"][0], trade_id="TRD-TEST0099-00000099")
                trades["trades"].insert(0, new)
                api.get(f"{API}/trades/my").respond(text=json.dumps(trades))
                async with asyncio.timeout(1.0):  # los del arranque llegan como PastFill (D-68)
                    fill = await anext(stream)
                    while not isinstance(fill, Fill):
                        fill = await anext(stream)
        assert fill.trade_id == "TRD-TEST0099-00000099"

    async def test_la_orden_enviada_conserva_su_client_id(self, limiter, api):
        """Neoxa no guarda client_id: el stream lo toma de lo que envió la terminal."""
        placed = json.loads(fixture("order_placed.json"))
        placed["order"]["order_id"] = "ORD-TEST0001-AAAA0001"
        api.post(f"{API}/order").respond(json=placed)
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter)
            await venue.place_limit(req())
            async with aclosing(venue.private_stream()) as stream:
                events = [await anext(stream) for _ in range(3)]
        mine = next(e for e in events if getattr(e, "order_id", "") == "ORD-TEST0001-AAAA0001")
        assert mine.client_id == "t-abc123"

    async def test_sin_keys_no_hay_stream(self, limiter):
        venue = NeoxaVenue("BTCB2_BTC", limiter)
        with pytest.raises(ReadOnlyError):
            async with aclosing(venue.private_stream()) as stream:
                await anext(stream)


@pytest.mark.spec("RNF-12")
class TestEnvioIncierto:
    async def test_timeout_tras_conectar(self, venue, api):
        api.post(f"{API}/order").mock(side_effect=httpx.ReadTimeout("t"))
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_sin_conectar_no_es_incierto(self, venue, api):
        api.post(f"{API}/order").mock(side_effect=httpx.ConnectError("c"))
        with pytest.raises(httpx.ConnectError):
            await venue.place_limit(req())

    async def test_5xx(self, venue, api):
        api.post(f"{API}/order").respond(503, json={"success": False, "error": "busy"})
        with pytest.raises(UncertainError, match="503"):
            await venue.place_limit(req())

    async def test_2xx_sin_orden(self, venue, api):
        api.post(f"{API}/order").respond(json={"success": True})
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_rechazo_claro(self, venue, api):
        api.post(f"{API}/order").respond(400, json={"success": False, "error": "no"})
        with pytest.raises(VenueError) as info:
            await venue.place_limit(req())
        assert not isinstance(info.value, UncertainError)


@pytest.mark.spec("RNF-12")
class TestOrdenesRecientes:
    def test_no_guarda_el_client_id(self, venue):
        assert venue.keeps_client_id is False  # PA-09

    async def test_activas_e_historial_del_par(self, venue, api, limiter):
        orders = await venue.recent_orders(NOW - 60_000)
        assert [(o.order_id, o.status) for o in orders] == [
            ("ORD-TEST0002-BBBB0002", "partial"),
            ("ORD-TEST0001-AAAA0001", "open"),
            ("ORD-TEST0001-AAAA0001", "cancelled"),
            ("ORD-TEST0003-CCCC0003", "filled"),
        ]
        assert limiter.kinds == ["private", "private"]


@pytest.mark.spec("RF-24", "RNF-10")
async def test_balances_con_token_de_fee(limiter, api):
    async with httpx.AsyncClient() as http:
        venue = NeoxaVenue(
            "BTCB2_BTC", limiter, http=http, credentials=CREDS, clock=lambda: NOW,
            fee_token="NEOXEX",
        )  # fmt: skip
        bal = await venue.balances()
    assert bal["NEOXEX"] == Balance("NEOXEX", D("0.0012345"), D("0"))
    assert set(bal) == {"BTC", "BTCB2", "NEOXEX"}
