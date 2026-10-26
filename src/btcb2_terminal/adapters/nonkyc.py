"""Adaptador de NonKYC (REST api.nonkyc.io + WS ws.nonkyc.io).

REST en `https://api.nonkyc.io/api/v2`; WS público JSON-RPC 2.0 en
`wss://ws.nonkyc.io` (D-10). Particularidades:

- Precios y cantidades como strings; `minQuote` y las velas como números JSON:
  se lee todo con `parse_float=Decimal`.
- El símbolo va como `BTCB2/BTC` en query y WS, y como `BTCB2_BTC` en rutas.
- `sequence` del book: el del WS sirve para encadenar deltas; el del REST es
  otro contador, así que los snapshots REST salen con `seq` None. Ante un hueco
  el adaptador se vuelve a suscribir y llega un snapshot nuevo.
- Sin velas de 1m: se construyen con los últimos trades REST (máx. 500).
- Fees no expuestas por API: vienen de la config (0.2 %/0.2 %, D-42).
- Privado REST firmado: `X-API-SIGN` = HMAC-SHA256(secret, key + url + body +
  nonce), con la url exacta (query incluida) y el body serializado una sola vez.
  El nonce (ms) se fuerza a crecer aunque dos peticiones caigan en el mismo ms.
- WS privado: `login` HS256 con firma HMAC-SHA256(secret, nonce) y
  `subscribeReports`. El formato de los `report` no está documentado y no se ha
  visto ninguno, así que solo despiertan un refresco REST de órdenes y trades
  propios (D-14). Sin WS, el refresco va por polling cada `private_every_s`.
- Un envío sin respuesta clara es incierto (D-26) y se reconcilia por
  `userProvidedId` con `recent_orders`.

Spec: RF-01, RF-05, RF-08, RF-09, RNF-05, RNF-12 · design.md §4 · Tareas: T-09, T-17, T-25
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator, Iterable, Mapping
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

REST_URL = "https://api.nonkyc.io/api/v2"
WS_URL = "wss://ws.nonkyc.io"
MAX_TRADES = 500  # tope real de /market/trades
RESOLUTION: dict[Timeframe, int] = {"5m": 5, "1h": 60, "4h": 240, "1d": 1440}  # minutos; sin 1m
REORDER_WAIT_S = 1.0  # cuánto se espera a un delta que llegó desordenado (D-10)
PRIVATE_EVERY_S = 3.0  # polling privado sin WS (RF-09)
PRIVATE_SAFETY_S = 15.0  # con el WS privado vivo, refresco de seguridad
MY_TRADES = 50
# ticker de depósito de cada activo del par: `BTC` a secas da 20010 "Must use
# more specific ticker"; BTC-MAIN es la red principal de Bitcoin (D-50)
DEPOSIT_TICKERS = {"BTC": "BTC-MAIN", "BTCB2": "BTCB2"}


def _loads(text: str) -> Any:
    return json.loads(text, parse_float=Decimal)


def _side(raw: Any) -> str:
    # `account/orders` dice "BUY"/"SELL"; `getorder` y los trades, "buy" (D-22)
    side = raw.lower() if isinstance(raw, str) else raw
    if side not in ("buy", "sell"):
        raise VenueError(f"NonKYC: lado de trade desconocido {raw!r}")
    return side


# --- firma --------------------------------------------------------------------


def sign(key: str, secret: str, url: str, body: str, nonce: str) -> str:
    return hmac.new(
        secret.encode(), f"{key}{url}{body}{nonce}".encode(), hashlib.sha256
    ).hexdigest()


def login_frame(key: str, secret: str, nonce: str, req_id: int) -> str:
    signature = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    params = {"algo": "HS256", "pKey": key, "nonce": nonce, "signature": signature}
    return json.dumps({"method": "login", "params": params, "id": req_id})


# --- parseo puro --------------------------------------------------------------


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
    """Mid de `getbysymbol` de otro mercado (`NKYC/BTC`), para valorar las fees
    pagadas en ese token (D-65). Sin bid o ask, o de otro mercado, es error."""
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
    """`quantity` llega como string, o como número 0 al borrar un nivel."""
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
    """Solo los activos pedidos, los del par y el token de fee si lo hay (RNF-10,
    D-63). `pending` son depósitos sin confirmar: ni se pueden usar ni están en
    órdenes, así que no cuentan."""
    raw = {b.get("asset"): b for b in payload}
    out: dict[str, Balance] = {}
    for asset in assets:
        b = raw.get(asset)
        out[asset] = Balance(asset, D(b["available"]), D(b["held"])) if b else Balance(asset, ZERO)
    return out


def parse_deposit(payload: dict[str, Any], asset: str, ticker: str) -> DepositAddress:
    """Respuesta de `getdepositaddress/{ticker}`; otro ticker o sin dirección es
    error: mejor ninguna dirección que una equivocada (RF-28)."""
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
    """Respuesta pública de `asset/getbyticker/{ticker}` (RF-29, D-53). BTC es
    el hijo `BTC-MAIN`: el padre `BTC` da otra comisión que no es la de la red
    principal. Supuesto (D-53): la comisión se descuenta de lo pedido; sin
    confirmar en su web."""
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
    """Vale para `Order` (createorder, getorder) y `Order2` (account/orders)."""
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
    """La fila dice ser de otro mercado. `/account/orders` lo da en `symbol` y
    `/account/trades` en `market.symbol`; sin ninguno, cuenta como del par."""
    market = raw.get("market")
    found = market.get("symbol") if isinstance(market, dict) else raw.get("symbol")
    return found is not None and found != symbol


def parse_fill(raw: dict[str, Any], quote: str, names: Mapping[str, str] | None = None) -> Fill:
    """La fee va en el activo de cotización, salvo si se pagó en otro: entonces
    `alternateFeeAsset` es su `assetid`, que `names` traduce al ticker (D-63)."""
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
    """Velas de 1m con los trades dados. Si la lista está cortada por el tope de
    la API, la vela más vieja puede estar incompleta y se descarta."""
    builder = CandleBuilder("1m", max_candles=limit + 1)
    for t in sorted(trades, key=lambda t: t.ts):
        builder.on_trade(t)
    candles = list(builder.candles)
    if truncated and candles and len(candles) <= limit:
        candles = candles[1:]
    return candles[-limit:]


# --- adaptador ----------------------------------------------------------------


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
        """`stream_opts`: opciones del supervisor de `StreamingVenue` (backoff, latido...).
        `fee_token`: su saldo sale con los del par, solo para leerlo (RF-24, D-63)."""
        super().__init__(ws_url=ws_url, book_depth=book_depth, **stream_opts)
        self.symbol = symbol
        self._assets = tuple(symbol.split("/"))
        self._balance_assets = (*self._assets, fee_token) if fee_token else self._assets
        self._asset_names: dict[str, str] = {}  # assetid → ticker, de `/balances`
        self._limiter = limiter
        self._maker_fee = maker_fee
        self._taker_fee = taker_fee
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._reorder_wait_s = reorder_wait_s
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
        """Petición firmada: la url y el body que se firman son los que viajan."""
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
        """Precio en el activo de cotización del par (BTC) del token de fee, por
        la API pública (RF-25, D-65)."""
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

    # -- privado ---------------------------------------------------------------

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
        """Órdenes abiertas del par. `status=active` también devuelve órdenes ya
        canceladas o llenas (D-59): se filtran por su estado. Las que desaparecen
        así las resuelve el seguimiento con `fetch_final`, como cualquier otra.
        El venue ignora `symbol` y trae todos los mercados (D-63)."""
        params = {"symbol": self.symbol, "status": "active", "limit": 500}
        payload = await self._signed("GET", "/account/orders", kind="private", params=params)
        now = self._clock()
        orders = self.parsing(
            lambda rs: [parse_order(r, now) for r in rs if not other_market(r, self.symbol)],
            payload,
        )
        return [o for o in orders if o.status in ("open", "partial")]

    async def fetch_fills(self) -> list[Fill]:
        """Trades propios del par; como en `fetch_active`, el venue trae todos los
        mercados aunque se le pase `symbol` (D-63)."""
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
        """`strictValidate`: si precio o cantidad no encajan en la rejilla, el venue
        rechaza en vez de truncar (RiskCheck ya lo ha comprobado)."""
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
        """Activas y, de los trades propios desde `since`, sus órdenes si no están
        activas: una orden llena al instante nunca sale como activa (D-26)."""
        active = await self.fetch_active()
        known = {o.order_id for o in active}
        recent = {f.order_id for f in await self.fetch_fills() if f.ts >= since and f.order_id}
        done = [await self.fetch_final(oid) for oid in sorted(recent - known)]
        return active + [o for o in done if o is not None]

    async def cancel(self, order_id: str) -> None:
        await self._signed("POST", "/cancelorder", kind="cancel", body={"id": order_id})

    async def cancel_all(self) -> None:
        """`/cancelallorders` con `side: "all"` (OpenAPI 1.3.0, `docs/api/nonkyc`).
        Sin `side` responde HTTP 200 con `400 Bad Request` (D-58). Sin
        `success: true` es error."""
        body = {"symbol": self.symbol, "side": "all"}
        payload = await self._signed("POST", "/cancelallorders", kind="cancel", body=body)
        if not (isinstance(payload, dict) and payload.get("success") is True):
            raise VenueError(f"NonKYC /cancelallorders sin success: {payload}")

    async def private_stream(self) -> AsyncIterator[PrivateEvent]:
        """Polling de órdenes y trades propios; el WS privado lo despierta con cada
        reporte y, mientras vive, el polling baja a `private_safety_s`."""
        self._keys()  # sin keys, ReadOnlyError antes de arrancar nada
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
        """Mantiene el WS privado: login, `subscribeReports` y un refresco por
        cada `activeOrders` o `report`. Reconecta con backoff; el contador de
        fallos vuelve a 0 cuando una sesión llega a recibir reportes."""
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
            wake.set()  # el refresco reemite el estado con la nueva fuente

    async def _private_session(self) -> AsyncIterator[None]:
        """Una sesión del WS privado: entrega un aviso por reporte; lanza al caer."""
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

    def _request(self, method: str, req_id: int) -> str:
        return json.dumps({"method": method, "params": {"symbol": self.symbol}, "id": req_id})

    async def _ws_events(self) -> AsyncIterator[MarketEvent]:
        """Sesión WS. Los deltas del book llegan a veces desordenados (91663 antes
        que 91662): uno adelantado espera hasta `reorder_wait_s` al que falta. Si
        no llega, es un hueco real: se entregan los pendientes (L2Book queda
        inválido) y se pide un snapshot nuevo volviendo a suscribirse."""
        async with self.ws_connect() as ws:
            await ws.send(self._request("subscribeOrderbook", 1))
            await ws.send(self._request("subscribeTrades", 2))
            seq: int | None = None  # último delta entregado; None = esperando snapshot
            pending: dict[int, BookDelta] = {}
            while True:
                try:
                    timeout = self._reorder_wait_s if pending else None
                    frame = await asyncio.wait_for(ws.recv(), timeout)
                except TimeoutError:
                    for delta in sorted(pending.values(), key=lambda d: d.seq or 0):
                        yield delta
                    pending.clear()
                    seq = None
                    await ws.send(self._request("subscribeOrderbook", 3))
                    continue
                msg = self.parsing(_loads, frame)
                if not isinstance(msg, dict):
                    raise VenueError(f"NonKYC WS: mensaje inesperado {str(frame)[:80]!r}")
                if msg.get("error"):
                    err = msg["error"]
                    raise VenueError(f"NonKYC WS: {err.get('code')} {err.get('message')}")
                params = msg.get("params")
                if not isinstance(params, dict) or params.get("symbol") != self.symbol:
                    continue  # respuestas a peticiones, otros símbolos
                method = msg.get("method")
                if method == "snapshotOrderbook":
                    snap = self.parsing(
                        parse_book, params, int(msg["timestamp"]), int(params["sequence"])
                    )
                    seq = snap.seq
                    pending = {k: d for k, d in pending.items() if seq is not None and k > seq}
                    yield snap
                    for delta in self._drain(pending, snap.seq):
                        seq = delta.seq
                        yield delta
                elif method == "updateOrderbook":
                    ts = params.get("timestamp")
                    delta = self.parsing(parse_delta, params, iso_ms(ts) if ts else self._clock())
                    if seq is None or delta.seq is None or delta.seq <= seq:
                        continue  # antes del snapshot, o viejo/duplicado
                    pending[delta.seq] = delta
                    for ready in self._drain(pending, seq):
                        seq = ready.seq
                        yield ready
                elif method in ("snapshotTrades", "updateTrades"):
                    raws = params.get("data") or []
                    parsed = self.parsing(lambda rs: [parse_ws_trade(r) for r in rs], raws)
                    for trade in self.fresh(parsed):
                        yield trade

    @staticmethod
    def _drain(pending: dict[int, BookDelta], seq: int | None) -> list[BookDelta]:
        """Saca de `pending` los deltas que encadenan con `seq`, en orden."""
        out: list[BookDelta] = []
        while seq is not None and seq + 1 in pending:
            seq += 1
            out.append(pending.pop(seq))
        return out
