"""Neoxa private REST (T-17, D-14) with fake keys. Fixtures shaped like the real
responses recorded on 2026-10-20 (IDs, user and amounts changed); place and cancel
come from the /api-docs examples, since they cannot be recorded without a real order."""

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


def req(side="buy", quantity="0.01", price="0.00725000"):
    return OrderRequest("nx", side, D(quantity), D(price), "t-abc123")


@pytest.mark.spec("RNF-09", "RF-12")
class TestCredentials:
    def test_fulfils_venue(self, limiter):
        assert isinstance(NeoxaVenue("BTCB2_BTC", limiter, credentials=CREDS), Venue)

    async def test_no_keys_venue_is_not_called(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, creds=None)
            with pytest.raises(ReadOnlyError):
                await venue.balances()
            with pytest.raises(ReadOnlyError):
                await venue.place_limit(req())
        assert not api.calls

    async def test_plain_headers(self, venue, api):
        await venue.balances()
        sent = api.calls.last.request
        assert sent.headers["X-API-Key"] == KEY and sent.headers["X-API-Secret"] == SECRET

    async def test_error_does_not_show_secret(self, venue, api):
        api.get(f"{API}/balances").respond(401, json={"success": False, "error": "Invalid API key"})
        with pytest.raises(VenueError) as err:
            await venue.balances()
        assert "Invalid API key" in str(err.value)
        assert SECRET not in str(err.value) and KEY not in str(err.value)


@pytest.mark.spec("RF-05", "RNF-10")
async def test_balances_only_btc_and_btcb2(venue, api, limiter):
    """Neoxa's `total` does not include what is committed in orders (`in_orders`)."""
    bal = await venue.balances()
    assert bal == {
        "BTC": Balance("BTC", D("0.0021"), D("0.00150005")),
        "BTCB2": Balance("BTCB2", D("0.25"), D("0.1")),
    }
    assert limiter.kinds == ["private"]


@pytest.mark.spec("RF-08")
class TestOrders:
    async def test_open_orders_listed(self, venue, api, limiter):
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

    async def test_send_limit_with_exact_numbers(self, venue, api, limiter):
        order = await venue.place_limit(req())
        body = api.calls.last.request.content.decode()
        assert body == '{"pair":"BTCB2_BTC","side":"buy","quantity":0.01,"price":0.00725000}'
        assert order == Order(
            "nx", "ORD-TEST0005-EEEE0005", "t-abc123", "buy", D("0.00725"), D("0.01"), D("0"),
            "open", NOW,
        )  # fmt: skip
        assert limiter.kinds == ["order"]

    async def test_venue_rejection(self, venue, api):
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

    async def test_cancel_order(self, venue, api, limiter):
        await venue.cancel("ORD-TEST0005-EEEE0005")
        sent = api.calls.last.request
        assert (sent.method, sent.url.path) == (
            "DELETE",
            "/api/exchange/order/ORD-TEST0005-EEEE0005",
        )
        assert limiter.kinds == ["cancel"]

    async def test_cancel_all_tries_all_even_if_one_fails(self, venue, api):
        def cancel(request):
            if request.url.path.endswith("ORD-TEST0002-BBBB0002"):
                return httpx.Response(400, json={"success": False, "error": "Order not found"})
            return httpx.Response(200, text=fixture("order_cancelled.json"))

        api["cancel"].side_effect = cancel
        with pytest.raises(VenueError, match="ORD-TEST0002-BBBB0002"):
            await venue.cancel_all()
        deleted = [call.request.url.path for call in api.calls if call.request.method == "DELETE"]
        assert deleted == [
            "/api/exchange/order/ORD-TEST0002-BBBB0002",
            "/api/exchange/order/ORD-TEST0001-AAAA0001",
        ]


@pytest.mark.spec("RF-09")
class TestFills:
    async def test_own_trades_of_the_pair(self, venue, api):
        fills = await venue.fetch_fills()
        assert [fill.trade_id for fill in fills] == [
            "TRD-TEST0001-00000001", "AMM-TEST0002-00000002", "TRD-TEST0003-00000003",
        ]  # fmt: skip
        assert fills[0] == Fill(
            "nx", "TRD-TEST0001-00000001", None, "buy", D("0.00725"), D("0.02683261"),
            D("3.9e-07"), "BTC", 1_792_531_543_116,
        )  # fmt: skip
        assert (fills[1].fee, fills[1].fee_asset) == (D("0"), None)  # AMM: no fee

    async def test_final_state_from_history(self, venue, api):
        final = await venue.fetch_final("ORD-TEST0001-AAAA0001")
        assert final is not None and (final.status, final.filled) == ("cancelled", D("0"))
        assert await venue.fetch_final("ORD-NO-EXISTE") is None

    async def test_history_mixes_no_other_pairs(self, venue, api):
        assert await venue.fetch_final("ORD-TEST0009-USDC0009") is None


@pytest.mark.spec("RF-09", "RNF-04", "RNF-03")
class TestPrivateStream:
    async def test_state_and_open_orders(self, venue, api):
        async with aclosing(venue.private_stream()) as stream:
            events = [await anext(stream) for _ in range(3)]
        state = events[0]
        assert isinstance(state, ConnState)
        assert (state.channel, state.source, state.status) == ("private", "rest", "up")
        assert [order.order_id for order in events[1:]] == [
            "ORD-TEST0002-BBBB0002",
            "ORD-TEST0001-AAAA0001",
        ]

    async def test_respects_rate_and_limiter(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, every=0.1)
            async with aclosing(venue.private_stream()) as stream:
                await anext(stream)
                await asyncio.sleep(0.35)
        polls = [call for call in api.calls if call.request.url.path == "/api/exchange/orders"]
        assert 3 <= len(polls) <= 5  # one at start and one every 0.1 s
        assert set(limiter.kinds) == {"private"}

    async def test_new_fill_between_refreshes(self, limiter, api):
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter, every=0.05)
            async with aclosing(venue.private_stream()) as stream:
                for _ in range(3):
                    await anext(stream)
                trades = json.loads(fixture("trades_my.json"))
                new = dict(trades["trades"][0], trade_id="TRD-TEST0099-00000099")
                trades["trades"].insert(0, new)
                api.get(f"{API}/trades/my").respond(text=json.dumps(trades))
                async with asyncio.timeout(1.0):  # those at startup arrive as PastFill (D-68)
                    fill = await anext(stream)
                    while not isinstance(fill, Fill):
                        fill = await anext(stream)
        assert fill.trade_id == "TRD-TEST0099-00000099"

    async def test_sent_order_keeps_client_id(self, limiter, api):
        """Neoxa keeps no client_id: the stream takes it from what the terminal sent."""
        placed = json.loads(fixture("order_placed.json"))
        placed["order"]["order_id"] = "ORD-TEST0001-AAAA0001"
        api.post(f"{API}/order").respond(json=placed)
        async with httpx.AsyncClient() as http:
            venue = make(http, limiter)
            await venue.place_limit(req())
            async with aclosing(venue.private_stream()) as stream:
                events = [await anext(stream) for _ in range(3)]
        mine = next(
            event for event in events if getattr(event, "order_id", "") == "ORD-TEST0001-AAAA0001"
        )
        assert mine.client_id == "t-abc123"

    async def test_no_keys_no_stream(self, limiter):
        venue = NeoxaVenue("BTCB2_BTC", limiter)
        with pytest.raises(ReadOnlyError):
            async with aclosing(venue.private_stream()) as stream:
                await anext(stream)


@pytest.mark.spec("RNF-12")
class TestUncertainSend:
    async def test_timeout_after_connecting(self, venue, api):
        api.post(f"{API}/order").mock(side_effect=httpx.ReadTimeout("t"))
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_unconnected_is_not_uncertain(self, venue, api):
        api.post(f"{API}/order").mock(side_effect=httpx.ConnectError("c"))
        with pytest.raises(httpx.ConnectError):
            await venue.place_limit(req())

    async def test_5xx(self, venue, api):
        api.post(f"{API}/order").respond(503, json={"success": False, "error": "busy"})
        with pytest.raises(UncertainError, match="503"):
            await venue.place_limit(req())

    async def test_2xx_without_order(self, venue, api):
        api.post(f"{API}/order").respond(json={"success": True})
        with pytest.raises(UncertainError):
            await venue.place_limit(req())

    async def test_clear_rejection(self, venue, api):
        api.post(f"{API}/order").respond(400, json={"success": False, "error": "no"})
        with pytest.raises(VenueError) as info:
            await venue.place_limit(req())
        assert not isinstance(info.value, UncertainError)


@pytest.mark.spec("RNF-12")
class TestRecentOrders:
    def test_does_not_keep_client_id(self, venue):
        assert venue.keeps_client_id is False  # PA-09

    async def test_active_and_history_of_the_pair(self, venue, api, limiter):
        orders = await venue.recent_orders(NOW - 60_000)
        assert [(order.order_id, order.status) for order in orders] == [
            ("ORD-TEST0002-BBBB0002", "partial"),
            ("ORD-TEST0001-AAAA0001", "open"),
            ("ORD-TEST0001-AAAA0001", "cancelled"),
            ("ORD-TEST0003-CCCC0003", "filled"),
        ]
        assert limiter.kinds == ["private", "private"]


@pytest.mark.spec("RF-24", "RNF-10")
async def test_balances_with_fee_token(limiter, api):
    async with httpx.AsyncClient() as http:
        venue = NeoxaVenue(
            "BTCB2_BTC", limiter, http=http, credentials=CREDS, clock=lambda: NOW,
            fee_token="NEOXEX",
        )  # fmt: skip
        bal = await venue.balances()
    assert bal["NEOXEX"] == Balance("NEOXEX", D("0.0012345"), D("0"))
    assert set(bal) == {"BTC", "BTCB2", "NEOXEX"}
