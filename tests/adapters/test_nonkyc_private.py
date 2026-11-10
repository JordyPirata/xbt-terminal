"""NonKYC private (T-17, D-14) with fake keys.

Balances and private WS messages are shaped like what was recorded with the real keys
on 2026-10-20 (amounts changed). Own orders and trades come from the OpenAPI v1.3.0
schema: the account had no orders on NonKYC that day and no real orders are sent to
record them."""

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


def req(side="buy", quantity="0.0100", price="0.00725000"):
    return OrderRequest("nk", side, D(quantity), D(price), "t-abc123")


@pytest.mark.spec("RF-12")
class TestSignature:
    def test_known_vector(self):
        """OpenAPI example (POST /cancelorder) with fixed keys and nonce."""
        url = "https://api.nonkyc.io/api/v2/cancelorder"
        got = sign(KEY, SECRET, url, '{"id":"5845454765457"}', "1792530500000")
        assert got == "373c92f4e4fba2fe45d7074fd515b7df3bc3ba7c5eb55d7fe1d827e16ba7b52d"

    def test_ws_login(self):
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

    async def test_get_signed_with_exact_url(self, venue, api):
        await venue.open_orders()
        sent = api.calls.last.request
        url = str(sent.url)
        assert url == f"{REST_URL}/account/orders?symbol=BTCB2%2FBTC&status=active&limit=500"
        assert sent.headers["X-API-KEY"] == KEY
        assert sent.headers["X-API-SIGN"] == expected_sign(url, "", sent.headers["X-API-NONCE"])

    async def test_post_signed_with_traveling_body(self, venue, api):
        await venue.place_limit(req())
        sent = api.calls.last.request
        body = sent.content.decode()
        assert body == (
            '{"symbol":"BTCB2/BTC","side":"buy","type":"limit","quantity":"0.0100",'
            '"price":"0.00725000","userProvidedId":"t-abc123","strictValidate":true}'
        )
        nonce = sent.headers["X-API-NONCE"]
        assert sent.headers["X-API-SIGN"] == expected_sign(f"{REST_URL}/createorder", body, nonce)

    async def test_nonce_always_grows(self, venue, api):
        for _ in range(3):
            await venue.balances()
        nonces = [int(call.request.headers["X-API-NONCE"]) for call in api.calls]
        assert nonces == [NOW, NOW + 1, NOW + 2]  # same clock: forced to grow

    async def test_no_keys_venue_is_not_called(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, creds=None)
            with pytest.raises(ReadOnlyError):
                await venue.balances()
        assert not api.calls

    async def test_error_without_secrets(self, venue, api):
        api.get(f"{REST_URL}/balances").respond(
            401, json={"error": {"code": 401, "message": "Authorisation failed"}}
        )
        with pytest.raises(VenueError, match="Authorisation failed") as err:
            await venue.balances()
        assert SECRET not in str(err.value) and KEY not in str(err.value)


@pytest.mark.spec("RF-05", "RNF-10")
async def test_balances_only_btc_and_btcb2(venue, api, limiter):
    """`held` is what is locked in orders; `pending` are unconfirmed deposits."""
    assert await venue.balances() == {
        "BTC": Balance("BTC", D("0.00210000"), D("0.00150005")),
        "BTCB2": Balance("BTCB2", D("0.25000000"), D("0.10000000")),
    }
    assert limiter.kinds == ["private"]


@pytest.mark.spec("RF-08")
class TestOrders:
    async def test_open_orders_listed(self, venue, api):
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

    async def test_active_with_some_executed_is_partial(self, venue, api):
        raw = json.loads(fixture("orders_active.json"))
        raw[0]["executedQuantity"] = "0.0500"
        api.get(f"{REST_URL}/account/orders").respond(json=raw)
        assert (await venue.open_orders())[0].status == "partial"

    async def test_active_without_finished_ones(self, venue, api):
        """T-48 (D-59): `status=active` also brings already cancelled orders (seen for real on
        2026-10-23, minutes after cancelling). Real shape of that order."""
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
        assert [order.order_id for order in orders] == [
            "6a0000000000000000000001", "6a0000000000000000000002",
        ]  # fmt: skip

    async def test_active_per_venue_even_if_it_says_inactive(self, venue, api):
        """`status` rules, not `isActive`: an `Active` with false `isActive` stays open (when in
        doubt, it counts as open for `self_cross` and the count)."""
        raw = json.loads(fixture("orders_active.json"))
        raw[0]["isActive"] = False
        api.get(f"{REST_URL}/account/orders").respond(json=raw)
        assert len(await venue.open_orders()) == 2

    async def test_unknown_status(self, venue, api):
        raw = json.loads(fixture("orders_active.json"))
        raw[0]["status"] = "Exploded"
        api.get(f"{REST_URL}/account/orders").respond(json=raw)
        with pytest.raises(VenueError, match="Exploded"):
            await venue.open_orders()

    @pytest.mark.parametrize("raw,side", [("BUY", "buy"), ("Sell", "sell"), ("buy", "buy")])
    def test_side_case_insensitive(self, raw, side):
        """D-22: `account/orders` says "BUY" and `getorder` "buy"; "BUY" used to kill each read
        of open orders and the real order did not show in the terminal."""
        order = json.loads(fixture("orders_active.json"))[0] | {"side": raw}
        assert parse_order(order, NOW).side == side
        trade = json.loads(fixture("trades_my.json"))[0] | {"side": raw}
        assert parse_fill(trade, "BTC").side == side

    @pytest.mark.parametrize("raw", ["x", None, ""])
    def test_unknown_side(self, raw):
        order = json.loads(fixture("orders_active.json"))[0] | {"side": raw}
        with pytest.raises(VenueError, match="lado"):
            parse_order(order, NOW)

    async def test_send_order(self, venue, api, limiter):
        order = await venue.place_limit(req())
        assert order == Order(
            "nk", "6a0000000000000000000005", "t-abc123", "buy", D("0.00725000"), D("0.0100"),
            D("0.0000"), "open", 1_792_530_500_000,
        )  # fmt: skip
        assert limiter.kinds == ["order"]

    async def test_venue_rejection(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(
            400, json={"error": {"code": 400, "message": "Insufficient funds"}}
        )
        with pytest.raises(VenueError, match="Insufficient funds"):
            await venue.place_limit(req())

    async def test_cancel_order(self, venue, api, limiter):
        await venue.cancel("6a0000000000000000000005")
        sent = api.calls.last.request
        assert sent.url.path == "/api/v2/cancelorder"
        assert sent.content == b'{"id":"6a0000000000000000000005"}'
        assert limiter.kinds == ["cancel"]

    async def test_cancel_all_of_the_pair(self, venue, api, limiter):
        """T-47 (D-58): the API asks `symbol` and `side`; without `side` it answered HTTP 200
        with `400 Bad Request` (seen for real on 2026-10-23). `all` comes from the OpenAPI."""
        await venue.cancel_all()
        sent = api.calls.last.request
        assert sent.url.path == "/api/v2/cancelallorders"
        assert sent.content == b'{"symbol":"BTCB2/BTC","side":"all"}'
        assert limiter.kinds == ["cancel"]

    async def test_cancel_all_with_error_inside_a_200(self, venue, api):
        api.post(f"{REST_URL}/cancelallorders").respond(json={"error": "400 Bad Request"})
        with pytest.raises(VenueError, match="400 Bad Request"):
            await venue.cancel_all()

    async def test_cancel_all_without_success(self, venue, api):
        api.post(f"{REST_URL}/cancelallorders").respond(json={"success": False, "ids": []})
        with pytest.raises(VenueError, match="cancelallorders"):
            await venue.cancel_all()

    async def test_final_state(self, venue, api):
        final = await venue.fetch_final("6a0000000000000000000001")
        assert final is not None and final.status == "cancelled"
        assert api.calls.last.request.url.path == "/api/v2/getorder/6a0000000000000000000001"


@pytest.mark.spec("RF-09")
async def test_fills_with_their_order(venue, api):
    fills = await venue.fetch_fills()
    assert fills[0] == Fill(
        "nk", "6b0000000000000000000001", "6a0000000000000000000002", "sell", D("0.00750000"),
        D("0.0250"), D("0.00000038"), "BTC", 1_792_530_420_000,
    )  # fmt: skip
    params = api.calls.last.request.url.params
    assert params["symbol"] == "BTCB2/BTC"


class FakePrivateWS:
    """WS server: checks the login and answers like NonKYC's."""

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
    return sum(1 for call in api.calls if call.request.url.path == "/api/v2/account/orders")


@pytest.mark.spec("RF-09", "RF-14")
class TestPrivateStream:
    async def test_login_and_reports_over_ws(self, limiter, api):
        server = FakePrivateWS()
        async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with httpx.AsyncClient() as http:
                venue = make(http, limiter, ws_url=f"ws://127.0.0.1:{port}", private_safety_s=60)
                async with aclosing(venue.private_stream()) as stream:
                    state = await next_state(stream)
                    if state.source == "rest":  # the first refresh may beat the login
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

    async def test_with_ws_alive_polling_drops_to_safety_rate(self, limiter, api):
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
                    await asyncio.sleep(0.3)  # without WS it would be ~15 refreshes
                    assert polls(api) == before

    async def test_rejected_login_stays_on_polling(self, limiter, api):
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

    async def test_no_keys_no_stream(self, limiter):
        venue = NonKYCVenue("BTCB2/BTC", limiter, maker_fee=D("0"), taker_fee=D("0"))
        assert isinstance(
            NonKYCVenue("BTCB2/BTC", limiter, maker_fee=D("0"), taker_fee=D("0")), Venue
        )
        with pytest.raises(ReadOnlyError):
            async with aclosing(venue.private_stream()) as stream:
                await anext(stream)


@pytest.mark.spec("RNF-12")
class TestUncertainSend:
    """D-26: the order may have been created and it is unknown → UncertainError; if surely
    not, VenueError or the connection error as is."""

    @pytest.mark.parametrize(
        "error", [httpx.ReadTimeout("t"), httpx.RemoteProtocolError("x"), httpx.ReadError("r")]
    )
    async def test_network_failure_after_connecting(self, venue, api, error):
        api.post(f"{REST_URL}/createorder").mock(side_effect=error)
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    @pytest.mark.parametrize(
        "error", [httpx.ConnectError("c"), httpx.ConnectTimeout("c"), httpx.PoolTimeout("p")]
    )
    async def test_unconnected_is_not_uncertain(self, venue, api, error):
        api.post(f"{REST_URL}/createorder").mock(side_effect=error)
        with pytest.raises(type(error)):
            await venue.place_limit(req())

    async def test_5xx(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(502, text="<html>Bad Gateway</html>")
        with pytest.raises(UncertainError, match="502"):
            await venue.place_limit(req())

    async def test_2xx_not_json(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(200, text="<html>ok</html>")
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_unreadable_2xx_as_order(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(json={"id": "x", "status": "Active"})
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_4xx_is_a_clear_rejection(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(400, text="<html>Bad Request</html>")
        with pytest.raises(VenueError) as info:
            await venue.place_limit(req())
        assert not isinstance(info.value, UncertainError)

    async def test_venue_error_with_200_is_clear_rejection(self, venue, api):
        api.post(f"{REST_URL}/createorder").respond(
            json={"error": {"code": 400, "message": "Insufficient funds"}}
        )
        with pytest.raises(VenueError) as info:
            await venue.place_limit(req())
        assert not isinstance(info.value, UncertainError)


@pytest.mark.spec("RNF-12")
class TestRecentOrders:
    def test_keeps_client_id(self, venue):
        assert venue.keeps_client_id is True

    async def test_active_without_new_trades(self, venue, api, limiter):
        orders = await venue.recent_orders(1_792_530_400_000)
        assert [order.order_id for order in orders] == [
            "6a0000000000000000000001",
            "6a0000000000000000000002",
        ]
        assert limiter.kinds == ["private", "private"]

    async def test_recent_trade_orders_asked_by_id(self, venue, api, limiter):
        """An order filled entirely at once is not among the active ones."""
        done = json.loads(fixture("order_cancelled.json")) | {
            "id": "6a00000000000000000000ff",
            "userProvidedId": "t-zzz999",
            "status": "Filled",
            "executedQuantity": "0.0100",
        }
        api.get(url__regex=rf"{REST_URL}/getorder/.+").respond(json=done)
        orders = await venue.recent_orders(1_792_510_000_000)
        assert [(order.order_id, order.client_id, order.status) for order in orders][-1] == (
            "6a00000000000000000000ff",
            "t-zzz999",
            "filled",
        )
        assert len(orders) == 3  # the active order's trade does not repeat the query
        assert api.calls.last.request.url.path.endswith("/getorder/6a00000000000000000000ff")
        assert limiter.kinds == ["private"] * 3


# Real NKYC/BTC shapes (2026-10-25): NonKYC ignores `symbol` in `/account/trades` and
# `/account/orders` and returns all markets (D-63).
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
    """The pair's responses with an NKYC/BTC trade and order in front."""
    trades = json.loads(fixture("trades_my.json"))
    orders = json.loads(fixture("orders_active.json"))
    api.get(f"{REST_URL}/account/trades").respond(json=[OTHER_TRADE, *trades])
    api.get(f"{REST_URL}/account/orders").respond(json=[OTHER_ORDER, *orders])
    return api


@pytest.mark.spec("RF-05", "RF-08", "RF-09")
class TestOtherMarkets:
    async def test_other_market_fills_stay_out(self, venue, mixed):
        fills = await venue.fetch_fills()
        assert [fill.trade_id for fill in fills] == [
            row["id"] for row in json.loads(fixture("trades_my.json"))
        ]

    async def test_other_market_active_orders_stay_out(self, venue, mixed):
        orders = await venue.fetch_active()
        assert [order.order_id for order in orders] == [
            "6a0000000000000000000001",
            "6a0000000000000000000002",
        ]

    async def test_no_symbol_in_row_counts_as_pair(self, venue, api):
        """Only what claims to be from another market is dropped."""
        bare = {
            k: value
            for k, value in json.loads(fixture("trades_my.json"))[0].items()
            if k != "market"
        }
        api.get(f"{REST_URL}/account/trades").respond(json=[bare])
        assert [fill.trade_id for fill in await venue.fetch_fills()] == [bare["id"]]

    async def test_recent_does_not_ask_other_market_orders(self, venue, mixed):
        orders = await venue.recent_orders(1_792_510_000_000)
        assert "6a00000000000000000000aa" not in {order.order_id for order in orders}
        paths = [call.request.url.path for call in mixed.calls]
        assert not any(
            request_path.endswith("/getorder/6a00000000000000000000aa") for request_path in paths
        )


NKYC_BALANCE = {
    "asset": "NKYC",
    "name": "NONKYC",
    "available": "4.54266766",
    "pending": "0.00000000",
    "held": "5.09230000",
    "assetid": "64369c724f86081e9d3f7fc9",
}


@pytest.mark.spec("RF-24", "RF-25", "RNF-10")
class TestFeeTokenSetup:
    @pytest.fixture
    async def token_venue(self, limiter, api):
        api.get(f"{REST_URL}/balances").respond(
            json=[*json.loads(fixture("balances.json")), NKYC_BALANCE]
        )
        async with httpx.AsyncClient() as http:
            yield make(http, limiter, fee_token="NKYC")

    async def test_token_balance_comes_with_the_pairs(self, token_venue):
        balances = await token_venue.balances()
        assert balances["NKYC"] == Balance("NKYC", D("4.54266766"), D("5.09230000"))
        assert set(balances) == {"BTC", "BTCB2", "NKYC"}

    async def test_no_token_not_shown(self, venue, api):
        api.get(f"{REST_URL}/balances").respond(
            json=[*json.loads(fixture("balances.json")), NKYC_BALANCE]
        )
        assert set(await venue.balances()) == {"BTC", "BTCB2"}

    async def test_token_fee_has_its_ticker(self, token_venue, api):
        """`alternateFeeAsset` is the `/balances` `assetid`, not the ticker."""
        mine = OTHER_TRADE | {"market": {"id": "x", "symbol": "BTCB2/BTC"}}
        api.get(f"{REST_URL}/account/trades").respond(json=[mine])
        await token_venue.balances()
        (fill,) = await token_venue.fetch_fills()
        assert (fill.fee, fill.fee_asset) == (D("0.0016640800"), "NKYC")

    async def test_no_read_balances_keeps_the_id(self, token_venue, api):
        mine = OTHER_TRADE | {"market": {"id": "x", "symbol": "BTCB2/BTC"}}
        api.get(f"{REST_URL}/account/trades").respond(json=[mine])
        (fill,) = await token_venue.fetch_fills()
        assert fill.fee_asset == "64369c724f86081e9d3f7fc9"


# real `/market/getbysymbol/NKYC_BTC` (2026-10-25), trimmed
NKYC_MARKET = {
    "symbol": "NKYC/BTC",
    "lastPrice": "0.00013089",
    "bestBid": "0.00013071",
    "bestAsk": "0.00013093",
}


@pytest.mark.spec("RF-24", "RF-25")
class TestTokenPrice:
    async def test_public_mid(self, venue, api, limiter):
        route = api.get(f"{REST_URL}/market/getbysymbol/NKYC_BTC").respond(json=NKYC_MARKET)
        assert await venue.token_price("NKYC") == (D("0.00013071") + D("0.00013093")) / 2
        assert route.called and "X-API-KEY" not in route.calls.last.request.headers
        assert limiter.kinds == ["market"]

    @pytest.mark.parametrize("missing", ["bestBid", "bestAsk"])
    async def test_one_side_missing_is_error(self, venue, api, missing):
        market = NKYC_MARKET | {missing: "0"}
        api.get(f"{REST_URL}/market/getbysymbol/NKYC_BTC").respond(json=market)
        with pytest.raises(VenueError, match="NKYC"):
            await venue.token_price("NKYC")

    async def test_other_market_in_response_is_error(self, venue, api):
        market = NKYC_MARKET | {"symbol": "BTCB2/BTC"}
        api.get(f"{REST_URL}/market/getbysymbol/NKYC_BTC").respond(json=market)
        with pytest.raises(VenueError, match="NKYC"):
            await venue.token_price("NKYC")
