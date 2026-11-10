"""Neoxa adapter (REST + public WS; private by polling).

REST at `https://neoxa.exchange/api`; public Socket.IO WS on the `/exchange`
namespace (D-09). Peculiarities:

- Numbers as JSON floats: read with `parse_float=Decimal`, never float.
- The book mixes real orders and AMM liquidity (`isAmm` → `Level.amm`). If a price
  appears in both, they add up and the level is marked AMM.
- Each WS `exchange:orderbook` is the full book, without `seq` or time: emitted as
  a `BookSnapshot` with local time.
- Trades deduplicated by `trade_id` (common base, `streaming.py`). `side` is taken
  as the taker side (D-09).
- Private with plain `X-API-Key` / `X-API-Secret` headers; Neoxa keys cannot
  withdraw. The private WS requires a web session JWT, so orders and fills are
  followed by polling every `private_every_s` (RNF-04, D-14).
- `/balances` gives `total` = `available` + `locked`, without what is committed in
  orders (`in_orders`): that is added to `Balance.locked`.
- Own trades do not say which order they belong to: `Fill.order_id` None (D-14).
- Orders are sent with numbers as exact text in the JSON, never through float.
- A send without a clear answer is uncertain (D-26); with no client_id on the venue
  (PA-09), it is reconciled by side, price and quantity with `recent_orders`.

Spec: RF-01, RF-05, RF-08, RF-09, RNF-03, RNF-04, RNF-12 · design.md §4-5 · Tasks: T-08, T-17, T-25
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import aclosing
from decimal import Decimal
from typing import Any

import httpx

from ..config import Credentials
from ..core.models import (
    ZERO,
    Balance,
    BookSnapshot,
    Candle,
    D,
    DepositAddress,
    Fill,
    Level,
    MarketSpec,
    Order,
    OrderRequest,
    OrderStatus,
    Timeframe,
    Trade,
    WithdrawalFee,
)
from . import socketio as sio
from .base import Limiter, MarketEvent, PrivateEvent, ReadOnlyError, VenueError, describe
from .private import OrderTracker, PrivateSource, fan_in, poll_private
from .streaming import StreamingVenue, iso_ms

REST_URL = "https://neoxa.exchange/api"
WS_URL = "wss://neoxa.exchange/socket.io/?EIO=4&transport=websocket"
NAMESPACE = "/exchange"
MAX_TRADES = 100  # limit of /exchange/trades/{pair}
MY_TRADES = 50  # own trades per refresh: plenty for 3 s
HISTORY = 100  # finished orders searched for one that disappeared
DEPOSIT_CHAINS = {"BTC": "bitcoin", "BTCB2": "btcb2"}  # keys of /deposits/addresses (D-50)
STATUSES: dict[str, OrderStatus] = {
    "open": "open",
    "partial": "partial",
    "filled": "filled",
    "cancelled": "cancelled",
}


# --- pure parsing --------------------------------------------------------------


def parse_market(payload: dict[str, Any], symbol: str) -> MarketSpec:
    pair = next((p for p in payload.get("pairs") or [] if p.get("pair") == symbol), None)
    if pair is None:
        raise VenueError(f"Neoxa: el par {symbol} no existe")
    base, quote = symbol.split("_")
    if (pair["baseToken"], pair["quoteToken"]) != (base, quote):
        raise VenueError(
            f"Neoxa: {symbol} cotiza {pair['baseToken']}/{pair['quoteToken']}, no {base}/{quote}"
        )
    return MarketSpec(
        venue="nx",
        symbol=symbol,
        base=base,
        quote=quote,
        tick_size=Decimal(1).scaleb(-int(pair["pricePrecision"])),
        lot_size=Decimal(1).scaleb(-int(pair["quantityPrecision"])),
        min_qty=D(pair["minQuantity"]),
        min_notional=ZERO,  # Neoxa has no minimum notional
        maker_fee=D(pair["makerFeeRate"]),
        taker_fee=D(pair["takerFeeRate"]),
    )


def _levels(raw: Iterable[dict[str, Any]], reverse: bool) -> tuple[Level, ...]:
    merged: dict[Decimal, Level] = {}
    for price_level in raw:
        price, qty, amm = (
            D(price_level["price"]),
            D(price_level["quantity"]),
            bool(price_level.get("isAmm")),
        )
        prev = merged.get(price)
        if prev is not None:
            qty, amm = prev.qty + qty, prev.amm or amm
        merged[price] = Level(price, qty, amm=amm)
    return tuple(
        sorted(merged.values(), key=lambda price_level: price_level.price, reverse=reverse)
    )


def parse_book(payload: dict[str, Any], ts: int) -> BookSnapshot:
    return BookSnapshot(
        venue="nx",
        bids=_levels(payload.get("bids") or [], reverse=True),
        asks=_levels(payload.get("asks") or [], reverse=False),
        ts=ts,
    )


def _side(raw: Any) -> str:
    if raw not in ("buy", "sell"):
        raise VenueError(f"Neoxa: lado de trade desconocido {raw!r}")
    return raw


def parse_trade(raw: dict[str, Any]) -> tuple[str, Trade]:
    """(trade_id, Trade). REST usa trade_id/executed_at; el WS, tradeId/time."""
    trade_id = raw.get("trade_id") or raw["tradeId"]
    ts = raw.get("executed_at") or raw["time"]
    return str(trade_id), Trade(
        venue="nx",
        price=D(raw["price"]),
        qty=D(raw["quantity"]),
        side=_side(raw.get("side")),  # type: ignore[arg-type]
        ts=iso_ms(ts),
    )


def parse_candles(payload: dict[str, Any]) -> list[Candle]:
    candles = [
        Candle(
            ts_open=int(c["time"]) * 1000,
            open=D(c["open"]),
            high=D(c["high"]),
            low=D(c["low"]),
            close=D(c["close"]),
            volume=D(c["volume"]),
        )
        for c in payload.get("candles") or []
    ]
    return sorted(candles, key=lambda c: c.ts_open)


def parse_balances(payload: dict[str, Any], assets: Iterable[str]) -> dict[str, Balance]:
    """Only the assets asked for, the pair's and the fee token if any (RNF-10, D-63):
    the rest, stablecoins included, is ignored."""
    raw = payload.get("balances") or {}
    out: dict[str, Balance] = {}
    for asset in assets:
        b = raw.get(asset)
        if b is None:
            out[asset] = Balance(asset, ZERO)
            continue
        out[asset] = Balance(asset, D(b["available"]), D(b["locked"]) + D(b["in_orders"]))
    return out


def parse_order(raw: dict[str, Any], client_id: str | None, now: int) -> Order:
    status = STATUSES.get(raw.get("status"))  # type: ignore[arg-type]
    if status is None:
        raise VenueError(f"Neoxa: estado de orden desconocido {raw.get('status')!r}")
    created = raw.get("created_at")
    return Order(
        venue="nx",
        order_id=str(raw["order_id"]),
        client_id=client_id,
        side=_side(raw.get("side")),  # type: ignore[arg-type]
        price=D(raw["price"]),
        qty=D(raw["quantity"]),
        filled=D(raw["quantity_filled"]),
        status=status,
        ts=iso_ms(created) if created else now,
    )


def parse_fill(raw: dict[str, Any]) -> Fill:
    return Fill(
        venue="nx",
        trade_id=str(raw["trade_id"]),
        order_id=None,
        side=_side(raw.get("side")),  # type: ignore[arg-type]
        price=D(raw["price"]),
        qty=D(raw["quantity"]),
        fee=D(raw.get("fee") or 0),
        fee_asset=raw.get("fee_token"),
        ts=iso_ms(raw["executed_at"]),
    )


def parse_deposit(payload: dict[str, Any], asset: str, chain: str) -> DepositAddress:
    """`/deposits/addresses` brings every chain of the account; a closed one or one
    without an address is an error, never an address (RF-28)."""
    closed = (payload.get("closedChains") or {}).get(chain)
    if closed:
        raise VenueError(f"Neoxa: depósitos de {chain} cerrados ({closed})")
    address = str((payload.get("addresses") or {}).get(chain) or "").strip()
    if not address:
        raise VenueError(f"Neoxa: {chain} sin dirección de depósito")
    return DepositAddress("nx", asset, chain, chain, address)


def parse_withdrawal_fee(payload: dict[str, Any], asset: str, chain: str) -> WithdrawalFee:
    """`/withdrawals/fees` brings every token; the asset's entry on its deposit chain
    counts (RF-29). Fee and minimum come as number or text. The fee is on top: its
    web asks 0.2, 0.2 arrives and 0.2001 is deducted (D-53)."""
    for f in payload.get("fees") or []:
        if f.get("token") == asset and f.get("chain") == chain:
            fee, minimum = f.get("withdrawalFee"), f.get("minWithdrawal")
            if fee in (None, ""):
                break
            return WithdrawalFee(
                "nx", asset, chain, D(str(fee)),
                minimum=D(str(minimum)) if minimum not in (None, "") else None, on_top=True,
            )  # fmt: skip
    raise VenueError(f"Neoxa: {asset} en {chain} sin comisión de retiro")


def order_body(symbol: str, order_request: OrderRequest) -> str:
    """JSON with price and quantity as numbers written exactly like the Decimal."""
    return (
        f'{{"pair":{json.dumps(symbol)},"side":{json.dumps(order_request.side)},'
        f'"quantity":{order_request.qty:f},"price":{order_request.price:f}}}'
    )


# --- adapter -------------------------------------------------------------------


class NeoxaVenue(StreamingVenue):
    id = "nx"
    name = "Neoxa"
    max_trades = MAX_TRADES
    keeps_client_id = False  # PA-09: reconciled by side, price and quantity

    def __init__(
        self,
        symbol: str,
        limiter: Limiter,
        *,
        http: httpx.AsyncClient | None = None,
        credentials: Credentials | None = None,
        private_every_s: float = 3.0,
        ws_url: str = WS_URL,
        book_depth: int = 50,
        fee_token: str | None = None,
        **stream_opts: Any,
    ) -> None:
        """`stream_opts`: `StreamingVenue` supervisor options (backoff, heartbeat...).
        `fee_token`: its balance comes with the pair's, just to read it (RF-24, D-63)."""
        super().__init__(ws_url=ws_url, book_depth=book_depth, **stream_opts)
        self.symbol = symbol
        self._assets = tuple(symbol.split("_"))
        self._balance_assets = (*self._assets, fee_token) if fee_token else self._assets
        self._limiter = limiter
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._credentials = credentials
        self._private_every_s = private_every_s
        self._tracker = OrderTracker()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- REST ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        kind: str = "market",
        params: dict[str, Any] | None = None,
        body: str | None = None,
        auth: bool = False,
    ) -> dict[str, Any]:
        headers = self._auth_headers() if auth else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        await self._limiter.acquire(kind)
        resp = await self.timed(
            self._http.request(
                method, REST_URL + path, params=params, content=body, headers=headers
            )
        )
        try:
            payload = json.loads(resp.text, parse_float=Decimal)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            raise VenueError(
                f"Neoxa {path}: HTTP {resp.status_code}, respuesta no JSON",
                status=resp.status_code,
                unreadable=True,
            )
        if resp.is_error or payload.get("success") is False:
            code = f" ({payload['code']})" if payload.get("code") else ""
            raise VenueError(
                f"Neoxa {path}: HTTP {resp.status_code}: {payload.get('error')}{code}",
                status=resp.status_code,
            )
        return payload

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    def _auth_headers(self) -> dict[str, str]:
        if self._credentials is None:
            raise ReadOnlyError("Neoxa: sin keys, solo lectura")
        return {
            "X-API-Key": self._credentials.api_key.reveal(),
            "X-API-Secret": self._credentials.api_secret.reveal(),
        }

    async def market(self) -> MarketSpec:
        return self.parsing(parse_market, await self._get("/exchange/pairs"), self.symbol)

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        payload = await self._get(f"/exchange/orderbook/{self.symbol}", {"depth": depth})
        return self.parsing(parse_book, payload, self._clock())

    async def _fetch_trades(self, limit: int) -> list[tuple[str, Trade]]:
        payload = await self._get(f"/exchange/trades/{self.symbol}", {"limit": limit})
        return self._parse_trades(payload.get("trades") or [])

    async def candles(self, tf: Timeframe, limit: int) -> list[Candle]:
        payload = await self._get(
            f"/exchange/candles/{self.symbol}", {"interval": tf, "limit": limit}
        )
        return self.parsing(parse_candles, payload)

    def _parse_trades(self, raws: Iterable[dict[str, Any]]) -> list[tuple[str, Trade]]:
        return self.parsing(lambda rs: [parse_trade(r) for r in rs], list(raws))

    # -- private ---------------------------------------------------------------

    async def _private(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, kind="private", params=params, auth=True)

    async def balances(self) -> dict[str, Balance]:
        payload = await self._private("/exchange/balances")
        return self.parsing(parse_balances, payload, self._balance_assets)

    async def deposit_address(self, asset: str) -> DepositAddress:
        chain = DEPOSIT_CHAINS.get(asset) if asset in self._assets else None
        if chain is None:
            raise VenueError(f"Neoxa: {asset} no es un activo del par")
        payload = await self._private("/deposits/addresses")
        return self.parsing(parse_deposit, payload, asset, chain)

    async def withdrawal_fee(self, asset: str) -> WithdrawalFee:
        chain = DEPOSIT_CHAINS.get(asset) if asset in self._assets else None
        if chain is None:
            raise VenueError(f"Neoxa: {asset} no es un activo del par")
        payload = await self._private("/withdrawals/fees")
        return self.parsing(parse_withdrawal_fee, payload, asset, chain)

    async def open_orders(self) -> list[Order]:
        return await self.fetch_active()

    async def fetch_active(self) -> list[Order]:
        params = {"status": "active", "pair": self.symbol, "limit": 500}
        payload = await self._private("/exchange/orders", params)
        now = self._clock()
        return self.parsing(
            lambda rs: [parse_order(r, None, now) for r in rs if r.get("pair") == self.symbol],
            payload.get("orders") or [],
        )

    async def fetch_fills(self) -> list[Fill]:
        payload = await self._private("/exchange/trades/my", {"limit": MY_TRADES})
        return self.parsing(
            lambda rs: [parse_fill(r) for r in rs if r.get("pair") == self.symbol],
            payload.get("trades") or [],
        )

    async def fetch_final(self, order_id: str) -> Order | None:
        payload = await self._private("/exchange/orders/history", {"limit": HISTORY})
        for raw in payload.get("orders") or []:
            if raw.get("order_id") == order_id and raw.get("pair") == self.symbol:
                return self.parsing(parse_order, raw, None, self._clock())
        return None

    async def place_limit(self, order_request: OrderRequest) -> Order:
        body = order_body(self.symbol, order_request)
        order = await self.sending(
            self._request("POST", "/exchange/order", kind="order", body=body, auth=True),
            lambda payload: parse_order(
                payload.get("order") or {}, order_request.client_id, self._clock()
            ),
        )
        self._tracker.remember(order)
        return order

    async def recent_orders(self, since: int) -> list[Order]:
        """Active ones and the last `HISTORY` finished of the pair; the reconciler applies
        the window (D-26). `since` is unused: the history is not filtered by date."""
        active = await self.fetch_active()
        payload = await self._private("/exchange/orders/history", {"limit": HISTORY})
        now = self._clock()
        history = self.parsing(
            lambda rs: [parse_order(r, None, now) for r in rs if r.get("pair") == self.symbol],
            payload.get("orders") or [],
        )
        return active + history

    async def cancel(self, order_id: str) -> None:
        await self._request("DELETE", f"/exchange/order/{order_id}", kind="cancel", auth=True)

    async def cancel_all(self) -> None:
        """Neoxa has no "cancel all": they are cancelled one by one, all attempted even
        if one fails."""
        failed: list[str] = []
        for order in await self.open_orders():
            try:
                await self.cancel(order.order_id)
            except (VenueError, httpx.HTTPError) as error:
                failed.append(f"{order.order_id}: {describe(error)}")
        if failed:
            raise VenueError("Neoxa: no se pudieron cancelar " + "; ".join(failed))

    async def private_stream(self) -> AsyncIterator[PrivateEvent]:
        self._auth_headers()  # without keys, ReadOnlyError before starting anything
        src = PrivateSource(self.fetch_active, self.fetch_fills, self.fetch_final)
        wake = asyncio.Event()

        def start(emit: Any) -> Any:
            return poll_private(
                "nx",
                self._tracker,
                src,
                emit,
                every_s=lambda: self._private_every_s,
                source=lambda: "rest",
                wake=wake,
                clock=self._clock,
            )

        async with aclosing(fan_in(start)) as events:
            async for event in events:
                yield event

    # -- WS --------------------------------------------------------------------

    async def _ws_events(self) -> AsyncIterator[MarketEvent]:
        async with self.ws_connect() as ws:
            opened = sio.decode(await asyncio.wait_for(ws.recv(), 10))
            if opened.kind != "open":
                raise VenueError(f"Neoxa WS: se esperaba open, llegó {opened.kind}")
            timeout = (opened.data["pingInterval"] + opened.data["pingTimeout"]) / 1000
            await ws.send(sio.connect_frame(NAMESPACE))
            subscribed = False
            while True:
                frame = await asyncio.wait_for(ws.recv(), timeout)
                try:
                    p = sio.decode(frame)
                except sio.ProtocolError as error:
                    raise VenueError(f"Neoxa WS: {error}") from error
                if p.kind == "ping":
                    await ws.send(sio.PONG)
                elif p.kind in ("close", "disconnect", "connect_error"):
                    raise VenueError(f"Neoxa WS: {p.kind} {p.data or ''}".strip())
                elif p.kind == "connect" and p.namespace == NAMESPACE and not subscribed:
                    await ws.send(sio.event_frame(NAMESPACE, "subscribe", {"pair": self.symbol}))
                    subscribed = True
                elif p.kind == "event" and p.namespace == NAMESPACE:
                    for event in self._on_event(p):
                        yield event

    def _on_event(self, p: sio.Packet) -> list[MarketEvent]:
        data = p.data if isinstance(p.data, dict) else {}
        if p.event == "exchange:error":
            raise VenueError(f"Neoxa WS: {data.get('message') or p.data}")
        if data.get("pair") != self.symbol:
            return []
        if p.event == "exchange:orderbook":
            return [self.parsing(parse_book, data, self._clock())]
        if p.event == "exchange:trades":
            return list(self.fresh(self._parse_trades(data.get("trades") or [])))
        if p.event == "exchange:trade":
            return list(self.fresh(self._parse_trades([data])))
        return []  # exchange:pool, pong and others are of no interest to the MVP
