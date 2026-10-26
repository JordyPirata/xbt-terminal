"""Privado de NonKYC (T-17, D-14) con claves falsas.

Balances y mensajes del WS privado tienen la forma de lo grabado con las keys
reales el 2026-10-20 (importes cambiados). Órdenes y trades propios salen del
esquema de la OpenAPI v1.3.0: la cuenta no tenía órdenes en NonKYC ese día y no
se envían órdenes reales para grabarlas."""

import asyncio
import hashlib
import hmac
import json
from contextlib import aclosing
from pathlib import Path

import httpx
import pytest
import respx
import websockets

from xbt_terminal.adapters.base import ReadOnlyError, UncertainError, Venue, VenueError
from xbt_terminal.adapters.nonkyc import (
    REST_URL,
    NonKYCVenue,
    login_frame,
    parse_fill,
    parse_order,
    sign,
)
from xbt_terminal.config import Credentials
from xbt_terminal.core.models import Balance, ConnState, D, Fill, Order, OrderRequest
from xbt_terminal.core.secrets import Secret

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nonkyc"
NOW = 1_792_530_500_000
KEY, SECRET = "nk-fake-key", "nk-fake-secret"
CREDS = Credentials(Secret(KEY), Secret(SECRET))


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def ws_frame(name: str) -> dict:
    return json.loads(fixture("ws_private.json"))[name]


class SpyLimiter:
    def __init__(self) -> None:
        self.kinds: list[str] = []

    async def acquire(self, kind: str) -> None:
        self.kinds.append(kind)


@pytest.fixture
def limiter():
    return SpyLimiter()


def make(http, limiter, creds=CREDS, ws_url="ws://127.0.0.1:9", **kw):
    return NonKYCVenue(
        "BTCB2/BTC",
        limiter,
        maker_fee=D("0.002"),
        taker_fee=D("0.002"),
        http=http,
        credentials=creds,
        ws_url=ws_url,
        clock=lambda: NOW,
        **kw,
    )


@pytest.fixture
async def venue(limiter):
    async with httpx.AsyncClient() as http:
        yield make(http, limiter)


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{REST_URL}/balances").respond(text=fixture("balances.json"))
        mock.get(f"{REST_URL}/account/orders").respond(text=fixture("orders_active.json"))
        mock.get(f"{REST_URL}/account/trades").respond(text=fixture("trades_my.json"))
        mock.get(url__regex=rf"{REST_URL}/getorder/.+").respond(
            text=fixture("order_cancelled.json")
        )
        mock.post(f"{REST_URL}/createorder").respond(text=fixture("order_created.json"))
        mock.post(f"{REST_URL}/cancelorder").respond(json={"success": True, "id": "x"})
        mock.post(f"{REST_URL}/cancelallorders").respond(json={"success": True, "ids": []})
        yield mock


def expected_sign(url: str, body: str, nonce: str) -> str:
    return hmac.new(
        SECRET.encode(), f"{KEY}{url}{body}{nonce}".encode(), hashlib.sha256
    ).hexdigest()


def req(side="buy", qty="0.0100", price="0.00725000"):
    return OrderRequest("nk", side, D(qty), D(price), "t-abc123")


@pytest.mark.spec("RF-12")
class TestFirma:
    def test_vector_conocido(self):
        """Ejemplo de la OpenAPI (POST /cancelorder) con claves y nonce fijos."""
        url = "https://api.nonkyc.io/api/v2/cancelorder"
        got = sign(KEY, SECRET, url, '{"id":"5845454765457"}', "1792530500000")
        assert got == "373c92f4e4fba2fe45d7074fd515b7df3bc3ba7c5eb55d7fe1d827e16ba7b52d"

    def test_login_del_ws(self):
        frame = json.loads(login_frame(KEY, SECRET, "0123456789abcdef0123", req_id=7))
        assert frame == {
            "method": "login",
            "params": {
                "algo": "HS256",
                "pKey": KEY,
                "nonce": "0123456789abcdef0123",
                "signature": "f8bf9c6293634bdef2ddaa147106b2fb20ccf75684f0efe562247a79c2e3ed49",
            },
            "id": 7,
        }

    async def test_get_firmado_con_la_url_exacta(self, venue, api):
        await venue.open_orders()
        sent = api.calls.last.request
        url = str(sent.url)
        assert url == f"{REST_URL}/account/orders?symbol=BTCB2%2FBTC&status=active&limit=500"
        assert sent.headers["X-API-KEY"] == KEY
        assert sent.headers["X-API-SIGN"] == expected_sign(url, "", sent.headers["X-API-NONCE"])

    async def test_post_firmado_con_el_cuerpo_que_viaja(self, venue, api):
        await venue.place_limit(req())
        sent = api.calls.last.request
        body = sent.content.decode()
        assert body == (
            '{"symbol":"BTCB2/BTC","side":"buy","type":"limit","quantity":"0.0100",'
            '"price":"0.00725000","userProvidedId":"t-abc123","strictValidate":true}'
        )
        nonce = sent.headers["X-API-NONCE"]
        assert sent.headers["X-API-SIGN"] == expected_sign(f"{REST_URL}/createorder", body, nonce)

    async def test_nonce_siempre_crece(self, venue, api):
        for _ in range(3):
            await venue.balances()
        nonces = [int(c.request.headers["X-API-NONCE"]) for c in api.calls]
        assert nonces == [NOW, NOW + 1, NOW + 2]  # mismo reloj: se fuerza a crecer

    async def test_sin_keys_no_se_llama_al_venue(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, creds=None)
            with pytest.raises(ReadOnlyError):
                await venue.balances()
        assert not api.calls

    async def test_error_sin_secretos(self, venue, api):
        api.get(f"{REST_URL}/balances").respond(
            401, json={"error": {"code": 401, "message": "Authorisation failed"}}
        )
        with pytest.raises(VenueError, match="Authorisation failed") as err:
            await venue.balances()
        assert SECRET not in str(err.value) and KEY not in str(err.value)


@pytest.mark.spec("RF-05", "RNF-10")
async def test_balances_solo_btc_y_btcb2(venue, api, limiter):
    """`held` es lo bloqueado en órdenes; `pending` son depósitos sin confirmar."""
    assert await venue.balances() == {
        "BTC": Balance("BTC", D("0.00210000"), D("0.00150005")),
        "BTCB2": Balance("BTCB2", D("0.25000000"), D("0.10000000")),
    }
    assert limiter.kinds == ["private"]


@pytest.mark.spec("RF-08")
class TestOrdenes:
    async def test_abiertas(self, venue, api):
        assert await venue.open_orders() == [
            Order(
                "nk", "6a0000000000000000000001", "t-abc123", "buy", D("0.00710000"),
                D("0.2000"), D("0.0000"), "open", 1_792_530_334_660,
            ),
            Order(
                "nk", "6a0000000000000000000002", None, "sell", D("0.00750000"),
                D("0.1000"), D("0.0250"), "partial", 1_792_530_360_000,
            ),
        ]  # fmt: skip

    async def test_activa_con_algo_ejecutado_es_parcial(self, venue, api):
        raw = json.loads(fixture("orders_active.json"))
        raw[0]["executedQuantity"] = "0.0500"
        api.get(f"{REST_URL}/account/orders").respond(json=raw)
        assert (await venue.open_orders())[0].status == "partial"

    async def test_activas_sin_las_ya_terminadas(self, venue, api):
        """T-48 (D-59): `status=active` también trae órdenes ya canceladas (visto en
        real el 2026-10-23, minutos después de cancelarlas). Forma real de esa orden."""
        cancelled = {
            "id": "6adb8a5b528f6c5d9013e239", "symbol": "BTCB2/BTC", "side": "BUY",
            "price": "0.0043", "quantity": "0.005", "executedQuantity": "0",
            "status": "Cancelled", "isActive": False,
            "timestamp": 1792772699960, "updatedAt": 1792772721506,
        }  # fmt: skip
        filled = json.loads(fixture("orders_active.json"))[1] | {
            "status": "Filled", "executedQuantity": "0.1000", "isActive": False,
        }  # fmt: skip
        raw = [*json.loads(fixture("orders_active.json")), cancelled, filled]
        api.get(f"{REST_URL}/account/orders").respond(json=raw)
        orders = await venue.open_orders()
        assert [o.order_id for o in orders] == [
            "6a0000000000000000000001", "6a0000000000000000000002",
        ]  # fmt: skip

    async def test_activa_segun_el_venue_aunque_diga_inactiva(self, venue, api):
        """Manda `status`, no `isActive`: una `Active` con `isActive` falso sigue abierta
        (ante la duda, cuenta como abierta para `self_cross` y el recuento)."""
        raw = json.loads(fixture("orders_active.json"))
        raw[0]["isActive"] = False
        api.get(f"{REST_URL}/account/orders").respond(json=raw)
        assert len(await venue.open_orders()) == 2

    async def test_estado_desconocido(self, venue, api):
        raw = json.loads(fixture("orders_active.json"))
        raw[0]["status"] = "Exploded"
        api.get(f"{REST_URL}/account/orders").respond(json=raw)
        with pytest.raises(VenueError, match="Exploded"):
            await venue.open_orders()

    @pytest.mark.parametrize("raw,side", [("BUY", "buy"), ("Sell", "sell"), ("buy", "buy")])
    def test_lado_sin_distinguir_mayusculas(self, raw, side):
        """D-22: `account/orders` dice "BUY" y `getorder` "buy"; antes "BUY" tiraba
        cada lectura de órdenes abiertas y la orden real no salía en la terminal."""
        order = json.loads(fixture("orders_active.json"))[0] | {"side": raw}
        assert parse_order(order, NOW).side == side
        trade = json.loads(fixture("trades_my.json"))[0] | {"side": raw}
        assert parse_fill(trade, "BTC").side == side

    @pytest.mark.parametrize("raw", ["x", None, ""])
    def test_lado_desconocido(self, raw):
        order = json.loads(fixture("orders_active.json"))[0] | {"side": raw}
        with pytest.raises(VenueError, match="lado"):
            parse_order(order, NOW)

    async def test_enviar(self, venue, api, limiter):
        order = await venue.place_limit(req())
        assert order == Order(
            "nk", "6a0000000000000000000005", "t-abc123", "buy", D("0.00725000"), D("0.0100"),
            D("0.0000"), "open", 1_792_530_500_000,
        )  # fmt: skip
        assert limiter.kinds == ["order"]

    async def test_rechazo_del_venue(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(
            400, json={"error": {"code": 400, "message": "Insufficient funds"}}
        )
        with pytest.raises(VenueError, match="Insufficient funds"):
            await venue.place_limit(req())

    async def test_cancelar(self, venue, api, limiter):
        await venue.cancel("6a0000000000000000000005")
        sent = api.calls.last.request
        assert sent.url.path == "/api/v2/cancelorder"
        assert sent.content == b'{"id":"6a0000000000000000000005"}'
        assert limiter.kinds == ["cancel"]

    async def test_cancelar_todas_del_par(self, venue, api, limiter):
        """T-47 (D-58): la API pide `symbol` y `side`; sin `side` respondía HTTP 200
        con `400 Bad Request` (visto en real el 2026-10-23). `all` es del OpenAPI."""
        await venue.cancel_all()
        sent = api.calls.last.request
        assert sent.url.path == "/api/v2/cancelallorders"
        assert sent.content == b'{"symbol":"BTCB2/BTC","side":"all"}'
        assert limiter.kinds == ["cancel"]

    async def test_cancelar_todas_con_error_dentro_de_un_200(self, venue, api):
        api.post(f"{REST_URL}/cancelallorders").respond(json={"error": "400 Bad Request"})
        with pytest.raises(VenueError, match="400 Bad Request"):
            await venue.cancel_all()

    async def test_cancelar_todas_sin_success(self, venue, api):
        api.post(f"{REST_URL}/cancelallorders").respond(json={"success": False, "ids": []})
        with pytest.raises(VenueError, match="cancelallorders"):
            await venue.cancel_all()

    async def test_estado_final(self, venue, api):
        final = await venue.fetch_final("6a0000000000000000000001")
        assert final is not None and final.status == "cancelled"
        assert api.calls.last.request.url.path == "/api/v2/getorder/6a0000000000000000000001"


@pytest.mark.spec("RF-09")
async def test_fills_con_su_orden(venue, api):
    fills = await venue.fetch_fills()
    assert fills[0] == Fill(
        "nk", "6b0000000000000000000001", "6a0000000000000000000002", "sell", D("0.00750000"),
        D("0.0250"), D("0.00000038"), "BTC", 1_792_530_420_000,
    )  # fmt: skip
    params = api.calls.last.request.url.params
    assert params["symbol"] == "BTCB2/BTC"


class FakePrivateWS:
    """Servidor WS: comprueba el login y responde como el de NonKYC."""

    def __init__(self, login_ok=True):
        self.login_ok = login_ok
        self.logins: list[dict] = []
        self.clients: list = []

    async def handler(self, ws):
        login = json.loads(await ws.recv())
        self.logins.append(login)
        if not self.login_ok:
            await ws.send(json.dumps(ws_frame("login_bad")))
            return
        await ws.send(json.dumps(ws_frame("login_ok")))
        sub = json.loads(await ws.recv())
        assert sub["method"] == "subscribeReports"
        await ws.send(json.dumps(dict(ws_frame("active_orders"), id=sub["id"])))
        self.clients.append(ws)
        await ws.wait_closed()

    async def report(self):
        for ws in self.clients:
            await ws.send(json.dumps({"jsonrpc": "2.0", "method": "report", "params": {}}))


async def next_state(stream, timeout=3.0):
    async with asyncio.timeout(timeout):
        while True:
            ev = await anext(stream)
            if isinstance(ev, ConnState):
                return ev


def polls(api):
    return sum(1 for c in api.calls if c.request.url.path == "/api/v2/account/orders")


@pytest.mark.spec("RF-09", "RF-14")
class TestStreamPrivado:
    async def test_login_y_reportes_por_ws(self, limiter, api):
        server = FakePrivateWS()
        async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with httpx.AsyncClient() as http:
                venue = make(http, limiter, ws_url=f"ws://127.0.0.1:{port}", private_safety_s=60)
                async with aclosing(venue.private_stream()) as stream:
                    state = await next_state(stream)
                    if state.source == "rest":  # el primer refresco puede ganar al login
                        state = await next_state(stream)
                    assert (state.channel, state.source, state.status) == ("private", "ws", "up")
                    before = polls(api)
                    await server.report()
                    async with asyncio.timeout(2):
                        while polls(api) == before:
                            await asyncio.sleep(0.01)
        login = server.logins[0]["params"]
        assert login["algo"] == "HS256" and login["pKey"] == KEY
        good = hmac.new(SECRET.encode(), login["nonce"].encode(), hashlib.sha256).hexdigest()
        assert login["signature"] == good

    async def test_con_ws_vivo_el_polling_baja_a_la_cadencia_de_seguridad(self, limiter, api):
        server = FakePrivateWS()
        async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with httpx.AsyncClient() as http:
                venue = make(
                    http,
                    limiter,
                    ws_url=f"ws://127.0.0.1:{port}",
                    private_every_s=0.02,
                    private_safety_s=60,
                )
                async with aclosing(venue.private_stream()) as stream:
                    while (await next_state(stream)).source != "ws":
                        pass
                    await asyncio.sleep(0.05)
                    before = polls(api)
                    await asyncio.sleep(0.3)  # sin WS serían ~15 refrescos
                    assert polls(api) == before

    async def test_login_rechazado_sigue_por_polling(self, limiter, api):
        server = FakePrivateWS(login_ok=False)
        async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with httpx.AsyncClient() as http:
                venue = make(http, limiter, ws_url=f"ws://127.0.0.1:{port}", private_every_s=0.05)
                async with aclosing(venue.private_stream()) as stream:
                    state = await next_state(stream)
                    await asyncio.sleep(0.3)
        assert (state.source, state.status) == ("rest", "up")
        assert polls(api) >= 3 and server.logins

    async def test_sin_keys_no_hay_stream(self, limiter):
        venue = NonKYCVenue("BTCB2/BTC", limiter, maker_fee=D("0"), taker_fee=D("0"))
        assert isinstance(
            NonKYCVenue("BTCB2/BTC", limiter, maker_fee=D("0"), taker_fee=D("0")), Venue
        )
        with pytest.raises(ReadOnlyError):
            async with aclosing(venue.private_stream()) as stream:
                await anext(stream)


@pytest.mark.spec("RNF-12")
class TestEnvioIncierto:
    """D-26: la orden pudo crearse y no se sabe → UncertainError; si seguro que no,
    VenueError o el error de conexión tal cual."""

    @pytest.mark.parametrize(
        "exc", [httpx.ReadTimeout("t"), httpx.RemoteProtocolError("x"), httpx.ReadError("r")]
    )
    async def test_fallo_de_red_tras_conectar(self, venue, api, exc):
        api.post(f"{REST_URL}/createorder").mock(side_effect=exc)
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    @pytest.mark.parametrize(
        "exc", [httpx.ConnectError("c"), httpx.ConnectTimeout("c"), httpx.PoolTimeout("p")]
    )
    async def test_sin_conectar_no_es_incierto(self, venue, api, exc):
        api.post(f"{REST_URL}/createorder").mock(side_effect=exc)
        with pytest.raises(type(exc)):
            await venue.place_limit(req())

    async def test_5xx(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(502, text="<html>Bad Gateway</html>")
        with pytest.raises(UncertainError, match="502"):
            await venue.place_limit(req())

    async def test_2xx_que_no_es_json(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(200, text="<html>ok</html>")
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_2xx_ilegible_como_orden(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(json={"id": "x", "status": "Active"})
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_4xx_es_un_rechazo_claro(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(400, text="<html>Bad Request</html>")
        with pytest.raises(VenueError) as info:
            await venue.place_limit(req())
        assert not isinstance(info.value, UncertainError)

    async def test_error_del_venue_con_200_es_un_rechazo_claro(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(
            json={"error": {"code": 400, "message": "Insufficient funds"}}
        )
        with pytest.raises(VenueError) as info:
            await venue.place_limit(req())
        assert not isinstance(info.value, UncertainError)


@pytest.mark.spec("RNF-12")
class TestOrdenesRecientes:
    def test_guarda_el_client_id(self, venue):
        assert venue.keeps_client_id is True

    async def test_activas_sin_trades_nuevos(self, venue, api, limiter):
        orders = await venue.recent_orders(1_792_530_400_000)
        assert [o.order_id for o in orders] == [
            "6a0000000000000000000001",
            "6a0000000000000000000002",
        ]
        assert limiter.kinds == ["private", "private"]

    async def test_las_de_trades_recientes_se_piden_por_id(self, venue, api, limiter):
        """Una orden llena del todo al instante no está entre las activas."""
        done = json.loads(fixture("order_cancelled.json")) | {
            "id": "6a00000000000000000000ff",
            "userProvidedId": "t-zzz999",
            "status": "Filled",
            "executedQuantity": "0.0100",
        }
        api.get(url__regex=rf"{REST_URL}/getorder/.+").respond(json=done)
        orders = await venue.recent_orders(1_792_510_000_000)
        assert [(o.order_id, o.client_id, o.status) for o in orders][-1] == (
            "6a00000000000000000000ff",
            "t-zzz999",
            "filled",
        )
        assert len(orders) == 3  # el trade de la orden activa no repite la consulta
        assert api.calls.last.request.url.path.endswith("/getorder/6a00000000000000000000ff")
        assert limiter.kinds == ["private"] * 3


# Formas reales de NKYC/BTC (2026-10-25): NonKYC ignora `symbol` en
# `/account/trades` y `/account/orders` y devuelve todos los mercados (D-63).
OTHER_TRADE = {
    "id": "6b00000000000000000000aa",
    "market": {"id": "6436ac93847698141b52f210", "symbol": "NKYC/BTC"},
    "orderid": "6a00000000000000000000aa",
    "side": "Buy",
    "triggeredBy": "sell",
    "price": "0.00013120",
    "quantity": "1.107",
    "fee": "0",
    "totalWithFee": "0.0001452384",
    "alternateFeeAsset": "64369c724f86081e9d3f7fc9",
    "alternateFee": "0.0016640800",
    "timestamp": 1_792_530_500_000,
}
OTHER_ORDER = {
    "id": "6a00000000000000000000bb",
    "userProvidedId": "09df05e5-5abc-48af-b33a-7877d5253295",
    "symbol": "NKYC/BTC",
    "side": "SELL",
    "price": "0.00013238",
    "quantity": "4.8174",
    "executedQuantity": "0",
    "status": "Active",
    "isActive": True,
    "timestamp": 1_792_530_500_000,
    "updatedAt": 1_792_530_500_100,
}


@pytest.fixture
def mixed(api):
    """Las respuestas del par con un trade y una orden de NKYC/BTC delante."""
    trades = json.loads(fixture("trades_my.json"))
    orders = json.loads(fixture("orders_active.json"))
    api.get(f"{REST_URL}/account/trades").respond(json=[OTHER_TRADE, *trades])
    api.get(f"{REST_URL}/account/orders").respond(json=[OTHER_ORDER, *orders])
    return api


@pytest.mark.spec("RF-05", "RF-08", "RF-09")
class TestOtrosMercados:
    async def test_los_fills_de_otro_mercado_no_salen(self, venue, mixed):
        fills = await venue.fetch_fills()
        assert [f.trade_id for f in fills] == [
            r["id"] for r in json.loads(fixture("trades_my.json"))
        ]

    async def test_las_activas_de_otro_mercado_no_salen(self, venue, mixed):
        orders = await venue.fetch_active()
        assert [o.order_id for o in orders] == [
            "6a0000000000000000000001",
            "6a0000000000000000000002",
        ]

    async def test_sin_symbol_en_la_fila_cuenta_como_del_par(self, venue, api):
        """Solo se descarta lo que dice ser de otro mercado."""
        bare = {k: v for k, v in json.loads(fixture("trades_my.json"))[0].items() if k != "market"}
        api.get(f"{REST_URL}/account/trades").respond(json=[bare])
        assert [f.trade_id for f in await venue.fetch_fills()] == [bare["id"]]

    async def test_recientes_no_pide_ordenes_de_otro_mercado(self, venue, mixed):
        orders = await venue.recent_orders(1_792_510_000_000)
        assert "6a00000000000000000000aa" not in {o.order_id for o in orders}
        paths = [c.request.url.path for c in mixed.calls]
        assert not any(p.endswith("/getorder/6a00000000000000000000aa") for p in paths)


NKYC_BALANCE = {
    "asset": "NKYC",
    "name": "NONKYC",
    "available": "4.54266766",
    "pending": "0.00000000",
    "held": "5.09230000",
    "assetid": "64369c724f86081e9d3f7fc9",
}


@pytest.mark.spec("RF-24", "RF-25", "RNF-10")
class TestTokenDeFee:
    @pytest.fixture
    async def token_venue(self, limiter, api):
        api.get(f"{REST_URL}/balances").respond(
            json=[*json.loads(fixture("balances.json")), NKYC_BALANCE]
        )
        async with httpx.AsyncClient() as http:
            yield make(http, limiter, fee_token="NKYC")

    async def test_el_saldo_del_token_sale_con_los_del_par(self, token_venue):
        balances = await token_venue.balances()
        assert balances["NKYC"] == Balance("NKYC", D("4.54266766"), D("5.09230000"))
        assert set(balances) == {"BTC", "BTCB2", "NKYC"}

    async def test_sin_token_no_sale(self, venue, api):
        api.get(f"{REST_URL}/balances").respond(
            json=[*json.loads(fixture("balances.json")), NKYC_BALANCE]
        )
        assert set(await venue.balances()) == {"BTC", "BTCB2"}

    async def test_la_fee_en_token_lleva_su_ticker(self, token_venue, api):
        """`alternateFeeAsset` es el `assetid` de `/balances`, no el ticker."""
        mine = OTHER_TRADE | {"market": {"id": "x", "symbol": "BTCB2/BTC"}}
        api.get(f"{REST_URL}/account/trades").respond(json=[mine])
        await token_venue.balances()
        (fill,) = await token_venue.fetch_fills()
        assert (fill.fee, fill.fee_asset) == (D("0.0016640800"), "NKYC")

    async def test_sin_saldos_leidos_queda_el_id(self, token_venue, api):
        mine = OTHER_TRADE | {"market": {"id": "x", "symbol": "BTCB2/BTC"}}
        api.get(f"{REST_URL}/account/trades").respond(json=[mine])
        (fill,) = await token_venue.fetch_fills()
        assert fill.fee_asset == "64369c724f86081e9d3f7fc9"


# `/market/getbysymbol/NKYC_BTC` real (2026-10-25), recortado
NKYC_MARKET = {
    "symbol": "NKYC/BTC",
    "lastPrice": "0.00013089",
    "bestBid": "0.00013071",
    "bestAsk": "0.00013093",
}


@pytest.mark.spec("RF-24", "RF-25")
class TestPrecioDelToken:
    async def test_mid_publico(self, venue, api, limiter):
        route = api.get(f"{REST_URL}/market/getbysymbol/NKYC_BTC").respond(json=NKYC_MARKET)
        assert await venue.token_price("NKYC") == (D("0.00013071") + D("0.00013093")) / 2
        assert route.called and "X-API-KEY" not in route.calls.last.request.headers
        assert limiter.kinds == ["market"]

    @pytest.mark.parametrize("missing", ["bestBid", "bestAsk"])
    async def test_sin_un_lado_es_error(self, venue, api, missing):
        market = NKYC_MARKET | {missing: "0"}
        api.get(f"{REST_URL}/market/getbysymbol/NKYC_BTC").respond(json=market)
        with pytest.raises(VenueError, match="NKYC"):
            await venue.token_price("NKYC")

    async def test_otro_mercado_en_la_respuesta_es_error(self, venue, api):
        market = NKYC_MARKET | {"symbol": "BTCB2/BTC"}
        api.get(f"{REST_URL}/market/getbysymbol/NKYC_BTC").respond(json=market)
        with pytest.raises(VenueError, match="NKYC"):
            await venue.token_price("NKYC")
