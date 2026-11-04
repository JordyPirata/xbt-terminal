"""NonKYC adapter (REST api.nonkyc.io + WS ws.nonkyc.io).

REST at `https://api.nonkyc.io/api/v2`; public JSON-RPC 2.0 WS at
`wss://ws.nonkyc.io` (D-10). Peculiarities:

- Prices and quantities as strings; `minQuote` and candles as JSON numbers: all read
  with `parse_float=Decimal`.
- The symbol is `BTCB2/BTC` in query and WS, and `BTCB2_BTC` in paths.
- The book's `sequence`: the WS one chains deltas; the REST one is another counter,
  so REST snapshots have `seq` None. On a gap the adapter resubscribes and a new
  snapshot arrives. Likewise with `resync()`, when the local book does not match
  REST (D-76).
- No 1m candles: built from the latest REST trades (max 500).
- Fees not exposed by API: they come from the config (0.2 %/0.2 %, D-42).
- Signed private REST: `X-API-SIGN` = HMAC-SHA256(secret, key + url + body + nonce),
  with the exact url (query included) and the body serialized only once. The nonce
  (ms) is forced to grow even if two requests fall in the same ms.
- Private WS: HS256 `login` signing HMAC-SHA256(secret, nonce) and `subscribeReports`.
  The `report` format is undocumented and none has been seen, so they only wake a
  REST refresh of own orders and trades (D-14). Without WS, the refresh polls every
  `private_every_s`.
- A send without a clear answer is uncertain (D-26) and reconciled by
  `userProvidedId` with `recent_orders`.

Spec: RF-01, RF-05, RF-08, RF-09, RNF-05, RNF-12, RNF-24 · design.md §4
Tasks: T-09, T-17, T-25, T-78
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import aclosing
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets

from ..config import Credentials
from ..core.candles import CandleBuilder
from ..core.models import (
    ZERO,
    Balance,
    BookDelta,
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
from .base import Limiter, MarketEvent, PrivateEvent, ReadOnlyError, VenueError
from .private import OrderTracker, PrivateSource, fan_in, poll_private
from .streaming import WS_CLOSE_TIMEOUT_S, WS_ERRORS, StreamingVenue, iso_ms

log = logging.getLogger(__name__)

REST_URL = "https://api.nonkyc.io/api/v2"
WS_URL = "wss://ws.nonkyc.io"
MAX_TRADES = 500  # real cap of /market/trades
RESOLUTION: dict[Timeframe, int] = {"5m": 5, "1h": 60, "4h": 240, "1d": 1440}  # minutes; no 1m
REORDER_WAIT_S = 1.0  # how long to wait for a delta that arrived out of order (D-10)
PRIVATE_EVERY_S = 3.0  # private polling without WS (RF-09)
PRIVATE_SAFETY_S = 15.0  # safety refresh with the private WS alive
MY_TRADES = 50
# deposit ticker of each pair asset: plain `BTC` gives 20010 "Must use more
# specific ticker"; BTC-MAIN is the Bitcoin main network (D-50)
DEPOSIT_TICKERS = {"BTC": "BTC-MAIN", "BTCB2": "BTCB2"}


def _loads(text: str) -> Any:
    return json.loads(text, parse_float=Decimal)


def _side(raw: Any) -> str:
    # `account/orders` says "BUY"/"SELL"; `getorder` and trades say "buy" (D-22)
    side = raw.lower() if isinstance(raw, str) else raw
    if side not in ("buy", "sell"):
        raise VenueError(f"NonKYC: lado de trade desconocido {raw!r}")
    return side


# --- signature -----------------------------------------------------------------


def sign(key: str, secret: str, url: str, body: str, nonce: str) -> str:
    return hmac.new(
        secret.encode(), f"{key}{url}{body}{nonce}".encode(), hashlib.sha256
    ).hexdigest()


def login_frame(key: str, secret: str, nonce: str, req_id: int) -> str:
    signature = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    params = {"algo": "HS256", "pKey": key, "nonce": nonce, "signature": signature}
    return json.dumps({"method": "login", "params": params, "id": req_id})


# --- pure parsing --------------------------------------------------------------


def parse_market(
    payload: dict[str, Any], symbol: str, maker_fee: Decimal, taker_fee: Decimal
) -> MarketSpec:
    if payload.get("symbol") != symbol:
        raise VenueError(f"NonKYC: se pidió {symbol} y el venue devolvió {payload.get('symbol')}")
    if not payload.get("isActive") or payload.get("isPaused"):
        raise VenueError(f"NonKYC: el mercado {symbol} está inactivo o pausado")
    base, quote = symbol.split("/")
    if payload.get("primaryTicker") != base:
        raise VenueError(f"NonKYC: {symbol} tiene base {payload.get('primaryTicker')}")
    lot = Decimal(1).scaleb(-int(payload["quantityDecimals"]))
    return MarketSpec(
        venue="nk",
        symbol=symbol,
        base=base,
        quote=quote,
        tick_size=Decimal(1).scaleb(-int(payload["priceDecimals"])),
        lot_size=lot,
        min_qty=max(D(payload.get("minimumQuantity") or 0), lot),
        min_notional=D(payload["minQuote"]) if payload.get("isMinQuoteActive") else ZERO,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )


def parse_token_price(payload: dict[str, Any], symbol: str) -> Decimal:
    """Mid of another market from `getbysymbol` (`NKYC/BTC`), to value fees paid in that
    token (D-65). Without bid or ask, or from another market, an error."""
    if payload.get("symbol") != symbol:
        raise VenueError(f"NonKYC: se pidió {symbol} y el venue devolvió {payload.get('symbol')}")
    bid, ask = D(payload.get("bestBid") or 0), D(payload.get("bestAsk") or 0)
    if bid <= 0 or ask <= 0:
        raise VenueError(f"NonKYC: {symbol} sin bid o sin ask")
    return (bid + ask) / 2


def _levels(raw: Iterable[dict[str, Any]], reverse: bool) -> tuple[Level, ...]:
    levels = [Level(D(lv["price"]), D(lv["quantity"])) for lv in raw]
    return tuple(sorted(levels, key=lambda lv: lv.price, reverse=reverse))


def parse_book(payload: dict[str, Any], ts: int, seq: int | None) -> BookSnapshot:
    return BookSnapshot(
        venue="nk",
        bids=_levels(payload.get("bids") or [], reverse=True),
        asks=_levels(payload.get("asks") or [], reverse=False),
        ts=ts,
        seq=seq,
    )


def parse_delta(params: dict[str, Any], ts: int) -> BookDelta:
    """`quantity` comes as a string, or as number 0 when deleting a level."""
    return BookDelta(
        venue="nk",
        bids=tuple(Level(D(lv["price"]), D(lv["quantity"])) for lv in params.get("bids") or []),
        asks=tuple(Level(D(lv["price"]), D(lv["quantity"])) for lv in params.get("asks") or []),
        ts=ts,
        seq=int(params["sequence"]),
    )


def parse_rest_trade(raw: dict[str, Any]) -> tuple[str, Trade]:
    return str(raw["id"]), Trade(
        venue="nk",
        price=D(raw["price"]),
        qty=D(raw["baseVolume"]),
        side=_side(raw.get("type")),  # type: ignore[arg-type]
        ts=int(raw["timestamp"]),
    )


def parse_ws_trade(raw: dict[str, Any]) -> tuple[str, Trade]:
    return str(raw["id"]), Trade(
        venue="nk",
        price=D(raw["price"]),
        qty=D(raw["quantity"]),
        side=_side(raw.get("side")),  # type: ignore[arg-type]
        ts=int(raw["timestampms"]),
    )


def ws_message(msg: Any, symbol: str, frame: str) -> dict[str, Any] | None:
    """The WS message if it is the pair's; None otherwise (replies to requests, other
    symbols). A venue error or a non-object ends the session with `VenueError`."""
    if not isinstance(msg, dict):
        raise VenueError(f"NonKYC WS: mensaje inesperado {str(frame)[:80]!r}")
    if msg.get("error"):
        err = msg["error"]
        raise VenueError(f"NonKYC WS: {err.get('code')} {err.get('message')}")
    params = msg.get("params")
    if not isinstance(params, dict) or params.get("symbol") != symbol:
        return None
    return msg


def parse_candles(payload: dict[str, Any]) -> list[Candle]:
    candles = [
        Candle(
            ts_open=int(b["time"]),
            open=D(b["open"]),
            high=D(b["high"]),
            low=D(b["low"]),
            close=D(b["close"]),
            volume=D(b["volume"]),
        )
        for b in payload.get("bars") or []
    ]
    return sorted(candles, key=lambda c: c.ts_open)


def parse_balances(payload: list[dict[str, Any]], assets: Iterable[str]) -> dict[str, Balance]:
    """Only the assets asked for, the pair's and the fee token if any (RNF-10, D-63).
    `pending` are unconfirmed deposits: neither usable nor in orders, so they do not
    count."""
    raw = {b.get("asset"): b for b in payload}
    out: dict[str, Balance] = {}
    for asset in assets:
        b = raw.get(asset)
        out[asset] = Balance(asset, D(b["available"]), D(b["held"])) if b else Balance(asset, ZERO)
    return out


def parse_deposit(payload: dict[str, Any], asset: str, ticker: str) -> DepositAddress:
    """Response of `getdepositaddress/{ticker}`; another ticker or no address is an
    error: better no address than a wrong one (RF-28)."""
    if payload.get("ticker") != ticker:
        raise VenueError(f"NonKYC: pedí {ticker} y la dirección es de {payload.get('ticker')}")
    address = str(payload.get("address") or "").strip()
    if not address:
        raise VenueError(f"NonKYC: {ticker} sin dirección de depósito")
    minimum, confs = payload.get("minimumDeposit"), payload.get("confirmsRequired")
    return DepositAddress(
        "nk", asset, ticker, str(payload.get("network") or ""), address,
        memo=str(payload.get("paymentid") or ""),
        min_deposit=D(str(minimum)) if minimum not in (None, "") else None,
        confirmations=int(confs) if confs is not None else None,
    )  # fmt: skip


def parse_withdrawal_fee(payload: dict[str, Any], asset: str, ticker: str) -> WithdrawalFee:
    """Public response of `asset/getbyticker/{ticker}` (RF-29, D-53). BTC is the child
    `BTC-MAIN`: the parent `BTC` gives another fee, not the main network's. Assumption
    (D-53): the fee is deducted from the amount; unconfirmed on its web."""
    if payload.get("ticker") != ticker:
        raise VenueError(f"NonKYC: pedí {ticker} y la comisión es de {payload.get('ticker')}")
    fee, minimum = payload.get("withdrawFee"), payload.get("minimumWithdraw")
    if fee in (None, ""):
        raise VenueError(f"NonKYC: {ticker} sin comisión de retiro")
    active = payload.get("withdrawalActive") is True and not payload.get("isMaintenance")
    return WithdrawalFee(
        "nk", asset, ticker, D(str(fee)),
        minimum=D(str(minimum)) if minimum not in (None, "") else None, active=active,
    )  # fmt: skip


def _status(raw: Any, executed: Decimal) -> OrderStatus:
    status = str(raw or "").lower()
    if status in ("active", "new"):
        return "partial" if executed > 0 else "open"
    if status in ("partly filled", "partially filled"):
        return "partial"
    if status in ("filled", "cancelled", "rejected"):
        return status  # type: ignore[return-value]
    if status == "canceled":
        return "cancelled"
    raise VenueError(f"NonKYC: estado de orden desconocido {raw!r}")


def parse_order(raw: dict[str, Any], now: int) -> Order:
    """Works for `Order` (createorder, getorder) and `Order2` (account/orders)."""
    executed = D(raw["executedQuantity"])
    ts = raw.get("timestamp") or raw.get("createdAt")
    return Order(
        venue="nk",
        order_id=str(raw["id"]),
        client_id=raw.get("userProvidedId") or None,
        side=_side(raw.get("side")),  # type: ignore[arg-type]
        price=D(raw["price"]),
        qty=D(raw["quantity"]),
        filled=executed,
        status=_status(raw.get("status"), executed),
        ts=int(ts) if ts else now,
    )


def other_market(raw: dict[str, Any], symbol: str) -> bool:
    """The row claims to be from another market. `/account/orders` gives it in `symbol`
    and `/account/trades` in `market.symbol`; with neither, it counts as the pair's."""
    market = raw.get("market")
    found = market.get("symbol") if isinstance(market, dict) else raw.get("symbol")
    return found is not None and found != symbol


def parse_fill(raw: dict[str, Any], quote: str, names: Mapping[str, str] | None = None) -> Fill:
    """The fee is in the quote asset, unless paid in another: then `alternateFeeAsset`
    is its `assetid`, which `names` turns into the ticker (D-63)."""
    alt = raw.get("alternateFeeAsset")
    if alt and names:
        alt = names.get(alt, alt)
    return Fill(
        venue="nk",
        trade_id=str(raw["id"]),
        order_id=str(raw["orderid"]),
        side=_side(raw.get("side")),  # type: ignore[arg-type]
        price=D(raw["price"]),
        qty=D(raw["quantity"]),
        fee=D(raw["alternateFee"]) if alt else D(raw.get("fee") or 0),
        fee_asset=alt or quote,
        ts=int(raw["timestamp"]),
    )


def candles_from_trades(trades: list[Trade], limit: int, truncated: bool) -> list[Candle]:
    """1m candles from the given trades. If the list is cut by the API cap, the oldest
    candle may be incomplete and is dropped."""
    builder = CandleBuilder("1m", max_candles=limit + 1)
    for t in sorted(trades, key=lambda t: t.ts):
        builder.on_trade(t)
    candles = list(builder.candles)
    if truncated and candles and len(candles) <= limit:
        candles = candles[1:]
    return candles[-limit:]


# --- adapter -------------------------------------------------------------------


class NonKYCVenue(StreamingVenue):
    id = "nk"
    name = "NonKYC"
    max_trades = MAX_TRADES
    keeps_client_id = True  # `userProvidedId`

    def __init__(
        self,
        symbol: str,
        limiter: Limiter,
        *,
        maker_fee: Decimal,
        taker_fee: Decimal,
        http: httpx.AsyncClient | None = None,
        credentials: Credentials | None = None,
        private_every_s: float = PRIVATE_EVERY_S,
        private_safety_s: float = PRIVATE_SAFETY_S,
        ws_url: str = WS_URL,
        book_depth: int = 100,
        reorder_wait_s: float = REORDER_WAIT_S,
        fee_token: str | None = None,
        **stream_opts: Any,
    ) -> None:
        """`stream_opts`: `StreamingVenue` supervisor options (backoff, heartbeat...).
        `fee_token`: its balance comes with the pair's, just to read it (RF-24, D-63)."""
        super().__init__(ws_url=ws_url, book_depth=book_depth, **stream_opts)
        self.symbol = symbol
        self._assets = tuple(symbol.split("/"))
        self._balance_assets = (*self._assets, fee_token) if fee_token else self._assets
        self._asset_names: dict[str, str] = {}  # assetid → ticker, from `/balances`
        self._limiter = limiter
        self._maker_fee = maker_fee
        self._taker_fee = taker_fee
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._reorder_wait_s = reorder_wait_s
        self._resync = False  # `resync()` asked for the book: the session awaits the snapshot
        self._credentials = credentials
        self._private_every_s = private_every_s
        self._private_safety_s = private_safety_s
        self._private_ws_up = False
        self._last_nonce = 0
        self._tracker = OrderTracker()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- REST ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._limiter.acquire("market")
        resp = await self.timed(self._http.get(REST_URL + path, params=params))
        return self._payload(path, resp)

    def _keys(self) -> tuple[str, str]:
        if self._credentials is None:
            raise ReadOnlyError("NonKYC: sin keys, solo lectura")
        return self._credentials.api_key.reveal(), self._credentials.api_secret.reveal()

    def _nonce(self) -> str:
        self._last_nonce = max(self._clock(), self._last_nonce + 1)
        return str(self._last_nonce)

    async def _signed(
        self,
        method: str,
        path: str,
        *,
        kind: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Signed request: the url and body signed are the ones that travel."""
        key, secret = self._keys()
        url = REST_URL + path + (f"?{urlencode(params)}" if params else "")
        text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        await self._limiter.acquire(kind)
        nonce = self._nonce()
        headers = {
            "X-API-KEY": key,
            "X-API-NONCE": nonce,
            "X-API-SIGN": sign(key, secret, url, text, nonce),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        resp = await self.timed(
            self._http.request(method, url, content=text or None, headers=headers)
        )
        return self._payload(path, resp)

    def _payload(self, path: str, resp: httpx.Response) -> Any:
        try:
            payload = _loads(resp.text)
        except json.JSONDecodeError:
            raise VenueError(
                f"NonKYC {path}: HTTP {resp.status_code}, respuesta no JSON",
                status=resp.status_code,
                unreadable=True,
            ) from None
        error = payload.get("error") if isinstance(payload, dict) else None
        if resp.is_error or error:
            if isinstance(error, dict):
                error = f"{error.get('code')} {error.get('message')}"
            raise VenueError(
                f"NonKYC {path}: HTTP {resp.status_code}: {error}", status=resp.status_code
            )
        return payload

    async def market(self) -> MarketSpec:
        payload = await self._get(f"/market/getbysymbol/{self.symbol.replace('/', '_')}")
        return self.parsing(parse_market, payload, self.symbol, self._maker_fee, self._taker_fee)

    async def token_price(self, asset: str) -> Decimal:
        """Price of the fee token in the pair's quote asset (BTC), through the public API
        (RF-25, D-65)."""
        symbol = f"{asset}/{self._assets[1]}"
        payload = await self._get(f"/market/getbysymbol/{symbol.replace('/', '_')}")
        return self.parsing(parse_token_price, payload, symbol)

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        payload = await self._get("/market/orderbook", {"symbol": self.symbol, "limit": depth})
        return self.parsing(lambda p: parse_book(p, int(p["timestamp"]), None), payload)

    async def _fetch_trades(self, limit: int) -> list[tuple[str, Trade]]:
        payload = await self._get("/market/trades", {"symbol": self.symbol, "limit": limit})
        return self.parsing(lambda rs: [parse_rest_trade(r) for r in rs], payload)

    async def candles(self, tf: Timeframe, limit: int) -> list[Candle]:
        if tf == "1m":
            raw = await self._fetch_trades(MAX_TRADES)
            trades = [t for _, t in raw]
            return candles_from_trades(trades, limit, truncated=len(raw) >= MAX_TRADES)
        params = {
            "symbol": self.symbol,
            "resolution": RESOLUTION[tf],
            "countBack": limit,
            "firstDataRequest": 1,
        }
        return self.parsing(parse_candles, await self._get("/market/candles", params))

    # -- private ---------------------------------------------------------------

    async def balances(self) -> dict[str, Balance]:
        payload = await self._signed("GET", "/balances", kind="private")
        balances = self.parsing(parse_balances, payload, self._balance_assets)
        self._asset_names = {
            b["assetid"]: b["asset"] for b in payload if b.get("assetid") and b.get("asset")
        }
        return balances

    async def deposit_address(self, asset: str) -> DepositAddress:
        ticker = DEPOSIT_TICKERS.get(asset) if asset in self._assets else None
        if ticker is None:
            raise VenueError(f"NonKYC: {asset} no es un activo del par")
        payload = await self._signed("GET", f"/getdepositaddress/{ticker}", kind="private")
        return self.parsing(parse_deposit, payload, asset, ticker)

    async def withdrawal_fee(self, asset: str) -> WithdrawalFee:
        ticker = DEPOSIT_TICKERS.get(asset) if asset in self._assets else None
        if ticker is None:
            raise VenueError(f"NonKYC: {asset} no es un activo del par")
        payload = await self._get(f"/asset/getbyticker/{ticker}")
        return self.parsing(parse_withdrawal_fee, payload, asset, ticker)

    async def open_orders(self) -> list[Order]:
        return await self.fetch_active()

    async def fetch_active(self) -> list[Order]:
        """Open orders of the pair. `status=active` also returns already cancelled or
        filled orders (D-59): filtered by their status. Those disappearing that way are
        resolved by the tracker with `fetch_final`, like any other. The venue ignores
        `symbol` and brings every market (D-63)."""
        params = {"symbol": self.symbol, "status": "active", "limit": 500}
        payload = await self._signed("GET", "/account/orders", kind="private", params=params)
        now = self._clock()
        orders = self.parsing(
            lambda rs: [parse_order(r, now) for r in rs if not other_market(r, self.symbol)],
            payload,
        )
        return [o for o in orders if o.status in ("open", "partial")]

    async def fetch_fills(self) -> list[Fill]:
        """Own trades of the pair; as in `fetch_active`, the venue brings every market even
        when given `symbol` (D-63)."""
        params = {"symbol": self.symbol, "limit": MY_TRADES}
        payload = await self._signed("GET", "/account/trades", kind="private", params=params)
        quote, names = self._assets[1], self._asset_names
        return self.parsing(
            lambda rs: [
                parse_fill(r, quote, names) for r in rs if not other_market(r, self.symbol)
            ],
            payload,
        )

    async def fetch_final(self, order_id: str) -> Order | None:
        payload = await self._signed("GET", f"/getorder/{order_id}", kind="private")
        return self.parsing(parse_order, payload, self._clock())

    async def place_limit(self, req: OrderRequest) -> Order:
        """`strictValidate`: if price or quantity do not fit the grid, the venue rejects
        instead of truncating (RiskCheck already checked)."""
        body = {
            "symbol": self.symbol,
            "side": req.side,
            "type": "limit",
            "quantity": f"{req.qty:f}",
            "price": f"{req.price:f}",
            "userProvidedId": req.client_id,
            "strictValidate": True,
        }
        order = await self.sending(
            self._signed("POST", "/createorder", kind="order", body=body),
            lambda payload: parse_order(payload, self._clock()),
        )
        self._tracker.remember(order)
        return order

    async def recent_orders(self, since: int) -> list[Order]:
        """Active ones and, of own trades since `since`, their orders if not active: an
        order filled instantly never shows as active (D-26)."""
        active = await self.fetch_active()
        known = {o.order_id for o in active}
        recent = {f.order_id for f in await self.fetch_fills() if f.ts >= since and f.order_id}
        done = [await self.fetch_final(oid) for oid in sorted(recent - known)]
        return active + [o for o in done if o is not None]

    async def cancel(self, order_id: str) -> None:
        await self._signed("POST", "/cancelorder", kind="cancel", body={"id": order_id})

    async def cancel_all(self) -> None:
        """`/cancelallorders` with `side: "all"` (OpenAPI 1.3.0, `docs/api/nonkyc`). Without
        `side` it answers HTTP 200 with `400 Bad Request` (D-58). Without `success: true`
        it is an error."""
        body = {"symbol": self.symbol, "side": "all"}
        payload = await self._signed("POST", "/cancelallorders", kind="cancel", body=body)
        if not (isinstance(payload, dict) and payload.get("success") is True):
            raise VenueError(f"NonKYC /cancelallorders sin success: {payload}")

    async def private_stream(self) -> AsyncIterator[PrivateEvent]:
        """Polling of own orders and trades; the private WS wakes it with each report and,
        while alive, polling drops to `private_safety_s`."""
        self._keys()  # without keys, ReadOnlyError before starting anything
        src = PrivateSource(self.fetch_active, self.fetch_fills, self.fetch_final)
        wake = asyncio.Event()

        def poll(emit: Any) -> Any:
            return poll_private(
                "nk",
                self._tracker,
                src,
                emit,
                every_s=lambda: (
                    self._private_safety_s if self._private_ws_up else self._private_every_s
                ),
                source=lambda: "ws" if self._private_ws_up else "rest",
                wake=wake,
                clock=self._clock,
            )

        def reports(emit: Any) -> Any:
            return self._private_ws(wake)

        async with aclosing(fan_in(poll, reports)) as events:
            async for event in events:
                yield event

    async def _private_ws(self, wake: asyncio.Event) -> None:
        """Keep the private WS: login, `subscribeReports` and a refresh for each
        `activeOrders` or `report`. Reconnects with backoff; the failure count returns to
        0 once a session gets to receive reports."""
        failures = 0
        while True:
            try:
                async for _ in self._private_session():
                    failures = 0
                    self._set_private_ws(True, wake)
                    wake.set()
            except WS_ERRORS:
                pass
            finally:
                self._set_private_ws(False, wake)
            failures += 1
            await asyncio.sleep(self._backoff.delay(failures))

    def _set_private_ws(self, up: bool, wake: asyncio.Event) -> None:
        if up != self._private_ws_up:
            self._private_ws_up = up
            wake.set()  # the refresh re-emits the state with the new source

    async def _private_session(self) -> AsyncIterator[None]:
        """One private WS session: gives a wake per report; raises when it drops."""
        key, secret = self._keys()
        async with websockets.connect(
            self._ws_url, open_timeout=10, close_timeout=WS_CLOSE_TIMEOUT_S, ping_interval=20
        ) as ws:
            await ws.send(login_frame(key, secret, secrets.token_hex(10), req_id=1))
            reply = self.parsing(_loads, await asyncio.wait_for(ws.recv(), 10))
            if not isinstance(reply, dict) or reply.get("result") is not True:
                err = reply.get("error") if isinstance(reply, dict) else None
                detail = f"{err.get('code')} {err.get('message')}" if isinstance(err, dict) else ""
                raise VenueError(f"NonKYC WS privado: login rechazado {detail}".strip())
            await ws.send(json.dumps({"method": "subscribeReports", "params": {}, "id": 2}))
            while True:
                msg = self.parsing(_loads, await ws.recv())
                if not isinstance(msg, dict):
                    continue
                if msg.get("error"):
                    err = msg["error"]
                    raise VenueError(f"NonKYC WS privado: {err.get('code')} {err.get('message')}")
                if msg.get("method") in ("activeOrders", "report"):
                    yield None

    # -- WS --------------------------------------------------------------------

    async def resync(self) -> None:
        """Ask for the book again on the same WS, as on a gap (D-76). What arrives until the
        snapshot is dropped: it could chain onto the bad book."""
        ws = self._ws
        if ws is None:
            return
        log.warning("%s: book differs from REST; asking for it again", self.name)
        self._resync = True
        await ws.send(self._request("subscribeOrderbook", 4))

    def _request(self, method: str, req_id: int) -> str:
        return json.dumps({"method": method, "params": {"symbol": self.symbol}, "id": req_id})

    async def _ws_events(self) -> AsyncIterator[MarketEvent]:
        """WS session. Book deltas sometimes arrive out of order (91663 before 91662): an
        early one waits up to `reorder_wait_s` for the missing one. If it does not arrive,
        it is a real gap: the pending ones are delivered (L2Book goes invalid) and a new
        snapshot is requested by resubscribing."""
        async with self.ws_connect() as ws:
            await ws.send(self._request("subscribeOrderbook", 1))
            await ws.send(self._request("subscribeTrades", 2))
            book = _BookOrder()
            while True:
                try:
                    timeout = self._reorder_wait_s if book.waiting else None
                    frame = await asyncio.wait_for(ws.recv(), timeout)
                except TimeoutError:
                    for delta in book.gap():
                        yield delta
                    await ws.send(self._request("subscribeOrderbook", 3))
                    continue
                if self._resync:
                    self._resync = False
                    book.reset()
                msg = ws_message(self.parsing(_loads, frame), self.symbol, frame)
                if msg is None:
                    continue
                for event in self._ws_message_events(book, msg):
                    yield event

    def _ws_message_events(self, book: _BookOrder, msg: dict[str, Any]) -> Sequence[MarketEvent]:
        """What comes out of a pair message: book in order or new trades."""
        method, params = msg.get("method"), msg["params"]
        if method == "snapshotOrderbook":
            snap = self.parsing(parse_book, params, int(msg["timestamp"]), int(params["sequence"]))
            return book.snapshot(snap)
        if method == "updateOrderbook":
            ts = params.get("timestamp")
            return book.delta(
                self.parsing(parse_delta, params, iso_ms(ts) if ts else self._clock())
            )
        if method in ("snapshotTrades", "updateTrades"):
            raws = params.get("data") or []
            return self.fresh(self.parsing(lambda rs: [parse_ws_trade(r) for r in rs], raws))
        return []


class _BookOrder:
    """Order of book deltas in a WS session: delivers each one when it chains onto the
    previous and keeps early ones until the missing one arrives."""

    def __init__(self) -> None:
        self.seq: int | None = None  # last delta delivered; None = awaiting a snapshot
        self.pending: dict[int, BookDelta] = {}

    @property
    def waiting(self) -> bool:
        """There are early deltas waiting for the missing one."""
        return bool(self.pending)

    def reset(self) -> None:
        """Await a new snapshot; what is pending is dropped."""
        self.seq = None
        self.pending.clear()

    def gap(self) -> list[BookDelta]:
        """Real gap: the pending ones, in order, then await a snapshot."""
        out = sorted(self.pending.values(), key=lambda d: d.seq or 0)
        self.reset()
        return out

    def snapshot(self, snap: BookSnapshot) -> list[MarketEvent]:
        """The snapshot and the pending ones that chain onto it."""
        self.seq = snap.seq
        after = snap.seq
        self.pending = {k: d for k, d in self.pending.items() if after is not None and k > after}
        return [snap, *self._drain()]

    def delta(self, delta: BookDelta) -> list[MarketEvent]:
        """Deltas ready for delivery; nothing if old or ahead of the snapshot."""
        if self.seq is None or delta.seq is None or delta.seq <= self.seq:
            return []
        self.pending[delta.seq] = delta
        return self._drain()

    def _drain(self) -> list[MarketEvent]:
        out: list[MarketEvent] = []
        while self.seq is not None and self.seq + 1 in self.pending:
            self.seq += 1
            out.append(self.pending.pop(self.seq))
        return out
