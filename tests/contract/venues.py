"""Harnesses of the real adapters: REST from tests/fixtures through respx, and a local
WS server replaying the recorded frames.

Own orders of NonKYC come from its OpenAPI v1.3.0, and placing and cancelling on Neoxa
from its /api-docs: recording them takes real orders (RF-36). Deposit and withdrawal
fixtures keep the shape recorded on 2026-10-22/23, with made-up addresses.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.utils import formatdate
from pathlib import Path
from typing import Any, ClassVar

import httpx
import respx
from websockets.asyncio.server import serve

from xbt_terminal.adapters.base import Depositor, TokenPricer, WithdrawalFees
from xbt_terminal.adapters.neoxa import REST_URL as NX_URL
from xbt_terminal.adapters.neoxa import NeoxaVenue
from xbt_terminal.adapters.nonkyc import REST_URL as NK_URL
from xbt_terminal.adapters.nonkyc import NonKYCVenue
from xbt_terminal.config import Credentials
from xbt_terminal.core.models import D
from xbt_terminal.core.secrets import Secret

from .harness import Harness, SpyLimiter

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = 1_792_530_500_000
KEY, SECRET = "contract-api-key", "contract-api-secret"
CREDS = Credentials(Secret(KEY), Secret(SECRET))
FAST_STREAM: dict[str, Any] = {
    "fallback_every_s": 0.01,
    "backoff_base_s": 0.01,
    "backoff_max_s": 0.02,
    "rng": lambda: 0.0,
}
UNREACHABLE_WS = "ws://127.0.0.1:9"


class RestHarness(Harness):
    """The respx and HTTP client plumbing of an adapter over REST."""

    folder: str
    secrets = (KEY, SECRET)
    bad_order: str  # readable JSON that is not an order
    rejection: ClassVar[dict[str, Any]]

    place: respx.Route
    cancel_route: respx.Route
    market_route: respx.Route
    orders_route: respx.Route
    my_trades_route: respx.Route

    def fixture(self, name: str) -> str:
        return (FIXTURES / self.folder / name).read_text(encoding="utf-8")

    def frames(self) -> dict[str, Any]:
        return json.loads(self.fixture("ws_frames.json"))

    def build(self, http: httpx.AsyncClient, **options: Any) -> Any:
        raise NotImplementedError

    def serve_rest(self, mock: respx.MockRouter) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> RestHarness:
        self.mock = respx.mock(assert_all_called=False)
        self.mock.start()
        self.serve_rest(self.mock)
        self.limiter = SpyLimiter()
        self.http = httpx.AsyncClient()
        self.venue = self.build(self.http, credentials=CREDS, private_every_s=0.01)
        self.read_only = self.build(self.http)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.http.aclose()
        self.mock.stop()

    def dead_stream_venue(self) -> Any:
        return self.build(self.http, ws_url=UNREACHABLE_WS, fallback_after=1, **FAST_STREAM)

    def venue_clock(self) -> int:
        return NOW

    def garble_market(self) -> None:
        self.market_route.side_effect = None
        self.market_route.respond(json={"success": True, "symbol": "?", "pairs": []})

    def date_market(self, ms: int) -> None:
        text = self.market_route.return_value.text
        date = formatdate(ms / 1000, usegmt=True)
        self.market_route.side_effect = lambda _: httpx.Response(
            200, text=text, headers={"date": date}
        )

    def send_outcome(self, outcome: str) -> None:
        self.place.side_effect = {
            "rejected": httpx.Response(400, json=self.rejection),
            "refused": httpx.ConnectError("refused"),
            "server_error": httpx.Response(502, json={"error": "bad gateway"}),
            "timeout": httpx.ReadTimeout("no reply"),
            "not_json": httpx.Response(200, text="<html>gateway</html>"),
            "unreadable": httpx.Response(200, text=self.bad_order),
        }[outcome]

    def sent_text(self) -> str:
        return self.place.calls.last.request.content.decode()

    def cancelled_text(self) -> str:
        request = self.cancel_route.calls.last.request
        return f"{request.url} {request.content.decode()}"

    def fail_cancel(self) -> None:
        self.cancel_route.side_effect = httpx.Response(400, json=self.rejection)

    def requests(self) -> int:
        return self.mock.calls.call_count


class NonKYCHarness(RestHarness):
    name = "nonkyc"
    folder = "nonkyc"
    implements = (Depositor, WithdrawalFees, TokenPricer)
    foreign_orders = ("6a00000000000000000000f1", "6a00000000000000000000f2")
    gone_order = "6a0000000000000000000001"
    bad_order = '{"unexpected": true}'
    rejection: ClassVar[dict[str, Any]] = {
        "error": {"code": 10001, "message": "Insufficient funds"}
    }

    def build(self, http: httpx.AsyncClient, **options: Any) -> NonKYCVenue:
        options.setdefault("ws_url", UNREACHABLE_WS)
        return NonKYCVenue(
            "BTCB2/BTC",
            self.limiter,
            maker_fee=D("0.002"),
            taker_fee=D("0.002"),
            http=http,
            clock=lambda: NOW,
            **options,
        )

    def active_orders(self) -> list[dict[str, Any]]:
        """As the real venue answers: cancelled orders (D-59) and every market (D-63)."""
        orders = json.loads(self.fixture("orders_active.json"))
        cancelled = {**orders[0], "id": self.foreign_orders[0], "status": "Cancelled"}
        other = {**orders[0], "id": self.foreign_orders[1], "symbol": "NKYC/BTC"}
        return [*orders, cancelled, other]

    def serve_rest(self, mock: respx.MockRouter) -> None:
        get, post = mock.get, mock.post
        self.market_route = get(f"{NK_URL}/market/getbysymbol/BTCB2_BTC").respond(
            text=self.fixture("market.json")
        )
        get(f"{NK_URL}/market/getbysymbol/NKYC_BTC").respond(text=self.fixture("market_nkyc.json"))
        get(f"{NK_URL}/market/orderbook").respond(text=self.fixture("orderbook.json"))
        get(f"{NK_URL}/market/trades").respond(text=self.fixture("trades.json"))
        for tf, resolution in (("5m", 5), ("1h", 60), ("4h", 240), ("1d", 1440)):
            get(f"{NK_URL}/market/candles", params={"resolution": resolution}).respond(
                text=self.fixture(f"candles_{tf}.json")
            )
        get(f"{NK_URL}/balances").respond(text=self.fixture("balances.json"))
        self.orders_route = get(f"{NK_URL}/account/orders").respond(json=self.active_orders())
        self.my_trades_route = get(f"{NK_URL}/account/trades").respond(
            text=self.fixture("trades_my.json")
        )
        get(url__regex=rf"{NK_URL}/getorder/.+").respond(text=self.fixture("order_cancelled.json"))
        self.place = post(f"{NK_URL}/createorder").respond(text=self.fixture("order_created.json"))
        self.cancel_route = post(f"{NK_URL}/cancelorder").respond(
            text=self.fixture("order_cancelled.json")
        )
        self.cancel_all_route = post(f"{NK_URL}/cancelallorders").respond(json={"success": True})
        for asset, ticker in (("btc", "BTC-MAIN"), ("btcb2", "BTCB2")):
            get(f"{NK_URL}/getdepositaddress/{ticker}", name=f"deposit-{ticker}").respond(
                text=self.fixture(f"deposit_{asset}.json")
            )
            get(f"{NK_URL}/asset/getbyticker/{ticker}").respond(
                text=self.fixture(f"asset_{asset}.json")
            )

    def cancel_all_reached_venue(self) -> bool:
        return self.cancel_all_route.called

    def new_own_trade(self) -> str:
        trades = json.loads(self.fixture("trades_my.json"))
        trades.insert(0, {**trades[0], "id": "6b00000000000000000000aa"})
        self.my_trades_route.respond(json=trades)
        return trades[0]["id"]

    def orders_gone(self) -> None:
        self.orders_route.respond(json=[])

    def blank_deposit(self) -> None:
        payload = json.loads(self.fixture("deposit_btcb2.json")) | {"address": " "}
        self.mock.routes["deposit-BTCB2"].respond(json=payload)

    @asynccontextmanager
    async def stream_venue(self, *, drop: bool = False) -> AsyncIterator[NonKYCVenue]:
        frames = self.frames()
        sessions = {"n": 0}

        async def handler(ws: Any) -> None:
            sessions["n"] += 1
            first = sessions["n"] == 1
            async for raw in ws:
                method = json.loads(raw)["method"]
                if method == "subscribeOrderbook":
                    await ws.send(frames["snapshotOrderbook"])
                elif method == "subscribeTrades":
                    await ws.send(frames["snapshotTrades"])
                    if first:
                        for update in frames["updateOrderbook"]:
                            await ws.send(update)
                    await ws.send(frames["updateTrades"])
                    if drop and first:
                        return

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            yield self.build(
                self.http, ws_url=f"ws://127.0.0.1:{port}", reorder_wait_s=0.05, **FAST_STREAM
            )


class NeoxaHarness(RestHarness):
    name = "neoxa"
    folder = "neoxa"
    implements = (Depositor, WithdrawalFees)
    foreign_orders = ("ORD-TEST0099-FFFF0099",)
    gone_order = "ORD-TEST0001-AAAA0001"
    bad_order = '{"success": true}'
    rejection: ClassVar[dict[str, Any]] = {
        "success": False,
        "error": "Insufficient balance",
        "code": "NO_FUNDS",
    }

    def build(self, http: httpx.AsyncClient, **options: Any) -> NeoxaVenue:
        return NeoxaVenue("BTCB2_BTC", self.limiter, http=http, clock=lambda: NOW, **options)

    def active_orders(self) -> dict[str, Any]:
        """Filtering by pair is the adapter's job: the fixture brings another one too."""
        payload = json.loads(self.fixture("orders_active.json"))
        first = payload["orders"][0]
        other = {**first, "order_id": self.foreign_orders[0], "pair": "NEOX_BTC"}
        return payload | {"orders": [*payload["orders"], other]}

    def serve_rest(self, mock: respx.MockRouter) -> None:
        get, api = mock.get, f"{NX_URL}/exchange"
        self.market_route = get(f"{api}/pairs").respond(text=self.fixture("pairs.json"))
        get(f"{api}/orderbook/BTCB2_BTC").respond(text=self.fixture("orderbook.json"))
        get(f"{api}/trades/BTCB2_BTC").respond(text=self.fixture("trades.json"))
        for tf in ("1m", "5m", "1h", "4h", "1d"):
            get(f"{api}/candles/BTCB2_BTC", params={"interval": tf}).respond(
                text=self.fixture(f"candles_{tf}.json")
            )
        get(f"{api}/balances").respond(text=self.fixture("balances.json"))
        self.orders_route = get(f"{api}/orders").respond(json=self.active_orders())
        get(f"{api}/orders/history").respond(text=self.fixture("orders_history.json"))
        self.my_trades_route = get(f"{api}/trades/my").respond(text=self.fixture("trades_my.json"))
        self.place = mock.post(f"{api}/order").respond(text=self.fixture("order_placed.json"))
        self.cancel_route = mock.delete(url__regex=rf"{api}/order/.+").respond(
            text=self.fixture("order_cancelled.json")
        )
        self.deposit_route = get(f"{NX_URL}/deposits/addresses").respond(
            text=self.fixture("deposit_addresses.json")
        )
        get(f"{NX_URL}/withdrawals/fees").respond(text=self.fixture("withdrawal_fees.json"))

    def cancel_all_reached_venue(self) -> bool:
        """No "cancel all" on Neoxa: one by one, the active orders of the pair."""
        paths = [call.request.url.path for call in self.cancel_route.calls]
        return len(paths) == 2 and not any(self.foreign_orders[0] in p for p in paths)

    def new_own_trade(self) -> str:
        payload = json.loads(self.fixture("trades_my.json"))
        trades = payload["trades"]
        trades.insert(0, {**trades[0], "trade_id": "TRD-CONTRACT-00000001"})
        self.my_trades_route.respond(json=payload)
        return trades[0]["trade_id"]

    def orders_gone(self) -> None:
        self.orders_route.respond(json={"success": True, "orders": []})

    def blank_deposit(self) -> None:
        self.deposit_route.respond(
            json={"success": True, "addresses": {"btcb2": " ", "bitcoin": ""}}
        )

    @asynccontextmanager
    async def stream_venue(self, *, drop: bool = False) -> AsyncIterator[NeoxaVenue]:
        frames = self.frames()
        sessions = {"n": 0}

        async def handler(ws: Any) -> None:
            sessions["n"] += 1
            await ws.send(frames["open"])
            await ws.recv()  # namespace connect
            await ws.send(frames["connect"])
            await ws.recv()  # subscribe
            await ws.send(frames["orderbook"])
            await ws.send(frames["trades"])
            if not (drop and sessions["n"] == 1):
                await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}/socket.io/?EIO=4&transport=websocket"
            yield self.build(self.http, ws_url=url, **FAST_STREAM)
