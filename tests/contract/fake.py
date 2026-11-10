"""An in-memory adapter that honours the whole contract, and one variant per clause
that breaks only that clause: they prove that each check can fail (CA-21)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import httpx

from xbt_terminal.adapters.base import (
    Depositor,
    ReadOnlyError,
    TokenPricer,
    UncertainError,
    VenueError,
    WithdrawalFees,
)
from xbt_terminal.core.models import (
    Balance,
    BookDelta,
    BookSnapshot,
    Candle,
    ConnState,
    D,
    DepositAddress,
    Fill,
    Level,
    MarketSpec,
    Order,
    OrderRequest,
    Trade,
    WithdrawalFee,
)

from .harness import Harness, SpyLimiter

NOW = 1_792_530_500_000
SECRET = "fake-api-secret"
SPEC = MarketSpec(
    "nk", "BTCB2/BTC", "BTCB2", "BTC", D("0.00000001"), D("0.0001"), D("0.0001"), D("0"),
    D("0.002"), D("0.002"),
)  # fmt: skip
ASSETS = ("BTCB2", "BTC")
TRADES = (
    Trade("nk", D("0.0072"), D("1"), "buy", NOW - 2000),
    Trade("nk", D("0.0073"), D("2"), "sell", NOW - 1000),
)


class FakeVenue:
    id = "nk"
    keeps_client_id = True

    def __init__(self, limiter: SpyLimiter, *, keys: bool = True, ws: str = "up") -> None:
        self.limiter = limiter
        self.keys = keys
        self.ws = ws  # up, drop or dead
        self.calls = 0
        self.clock_skew_ms: int | None = None
        self.market_payload: MarketSpec | None = SPEC
        self.date: int | None = None
        self.outcome: str | None = None
        self.sent = ""
        self.cancels: list[str] = []
        self.cancel_fails = False
        self.cancelled_all = False
        self.resyncs: asyncio.Queue[BookSnapshot] = asyncio.Queue()
        self.fills: list[Fill] = []
        self.active: list[Order] = [self.order("buy", "0.2", "0.0071", "A1", "open")]
        self.address = "bc1qfake"

    def order(self, side: Any, qty: str, price: str, oid: str, status: Any) -> Order:
        return Order("nk", oid, "t-abc123", side, D(price), D(qty), D("0"), status, NOW)

    def book(self) -> BookSnapshot:
        bids = (Level(D("0.0072"), D("1")), Level(D("0.0071"), D("2")))
        asks = (Level(D("0.0073"), D("1")), Level(D("0.0074"), D("2")))
        return BookSnapshot("nk", bids, asks, NOW, seq=10)

    def state(self, status: Any, source: Any = "ws", channel: Any = "market") -> ConnState:
        return ConnState("nk", channel, source, status, None, since=NOW)

    async def hit(self, kind: str) -> None:
        await self.limiter.acquire(kind)
        self.calls += 1

    def need_keys(self) -> None:
        if not self.keys:
            raise ReadOnlyError("fake: sin keys")

    # -- public --------------------------------------------------------------

    async def market(self) -> MarketSpec:
        await self.hit("market")
        if self.market_payload is None:
            raise VenueError("fake: respuesta inesperada")
        if self.date is not None:
            self.clock_skew_ms = self.date - NOW
        return self.market_payload

    async def book_snapshot(self, depth: int) -> BookSnapshot:
        await self.hit("market")
        return self.book()

    async def recent_trades(self, limit: int) -> list[Trade]:
        await self.hit("market")
        return list(TRADES)

    async def candles(self, tf: Any, limit: int) -> list[Candle]:
        await self.hit("market")
        return [
            Candle(NOW - 2000, D("1"), D("3"), D("1"), D("2"), D("5")),
            Candle(NOW - 1000, D("2"), D("3"), D("1"), D("2"), D("5")),
        ]

    async def market_stream(self) -> AsyncIterator[Any]:
        if self.ws == "dead":
            yield self.state("reconnecting")
            yield self.state("up", "rest")
            yield self.book()
        else:
            yield self.state("up")
            yield self.book()
            yield BookDelta("nk", (Level(D("0.0072"), D("3")),), (), NOW, seq=11)
            for trade in TRADES:
                yield trade
            if self.ws == "drop":
                yield self.state("reconnecting")
                yield self.state("up")
                yield self.book()
        while True:
            yield await self.resyncs.get()

    async def resync(self) -> None:
        self.resyncs.put_nowait(self.book())

    async def aclose(self) -> None:
        pass

    # -- private -------------------------------------------------------------

    async def balances(self) -> dict[str, Balance]:
        self.need_keys()
        await self.hit("private")
        return {
            "BTCB2": Balance("BTCB2", D("1")),
            "BTC": Balance("BTC", D("0.5"), D("0.00150005")),
        }

    async def open_orders(self) -> list[Order]:
        self.need_keys()
        await self.hit("private")
        return list(self.active)

    async def place_limit(self, order_request: OrderRequest) -> Order:
        self.need_keys()
        await self.hit("order")
        self.sent = f"{order_request.price:f} {order_request.qty:f}"
        outcome, self.outcome = self.outcome, None
        if outcome == "rejected":
            raise VenueError("fake: rechazada", status=400)
        if outcome == "refused":
            raise httpx.ConnectError("refused")
        if outcome is not None:
            raise UncertainError(f"fake: {outcome}")
        return Order(
            "nk",
            "B2",
            order_request.client_id,
            order_request.side,
            order_request.price,
            order_request.qty,
            D("0"),
            "open",
            NOW,
        )

    async def cancel(self, order_id: str) -> None:
        self.need_keys()
        await self.hit("cancel")
        self.cancels.append(order_id)
        if self.cancel_fails:
            raise VenueError("fake: no se pudo cancelar", status=400)

    async def cancel_all(self) -> None:
        self.need_keys()
        await self.hit("cancel")
        self.cancelled_all = True

    async def recent_orders(self, since: int) -> list[Order]:
        self.need_keys()
        await self.hit("private")
        return list(self.active)

    async def private_stream(self) -> AsyncIterator[Any]:
        self.need_keys()
        known = {open_item.order_id: open_item for open_item in self.active}
        yield self.state("up", "rest", "private")
        sent = 0
        while True:
            await asyncio.sleep(0.01)
            for fill in self.fills[sent:]:
                yield fill
            sent = len(self.fills)
            for oid, order in list(known.items()):
                if oid not in {open_item.order_id for open_item in self.active}:
                    del known[oid]
                    yield replace(order, status="cancelled")

    # -- optional ------------------------------------------------------------

    async def deposit_address(self, asset: str) -> DepositAddress:
        self.need_keys()
        if asset not in ASSETS:
            raise VenueError(f"fake: {asset} no es del par")
        if not self.address.strip():
            raise VenueError("fake: sin dirección")
        return DepositAddress("nk", asset, asset, "main", self.address)

    async def withdrawal_fee(self, asset: str) -> WithdrawalFee:
        if asset not in ASSETS:
            raise VenueError(f"fake: {asset} no es del par")
        return WithdrawalFee("nk", asset, asset, D("0.0001"))

    async def token_price(self, asset: str) -> Any:
        return D("0.000001")


class FakeHarness(Harness):
    name = "fake"
    implements = (Depositor, WithdrawalFees, TokenPricer)
    secrets = (SECRET,)
    gone_order = "A1"

    def __init__(self, cls: type[FakeVenue] = FakeVenue) -> None:
        self.cls = cls

    async def __aenter__(self) -> FakeHarness:
        self.limiter = SpyLimiter()
        self.venue = self.cls(self.limiter)
        self.read_only = self.cls(self.limiter, keys=False)
        return self

    async def __aexit__(self, *error: object) -> None:
        pass

    @asynccontextmanager
    async def stream_venue(self, *, drop: bool = False) -> AsyncIterator[FakeVenue]:
        yield self.cls(self.limiter, ws="drop" if drop else "up")

    def dead_stream_venue(self) -> FakeVenue:
        return self.cls(self.limiter, ws="dead")

    def garble_market(self) -> None:
        self.venue.market_payload = None

    def venue_clock(self) -> int:
        return NOW

    def date_market(self, ms: int) -> None:
        self.venue.date = ms

    def send_outcome(self, outcome: str) -> None:
        self.venue.outcome = outcome

    def sent_text(self) -> str:
        return self.venue.sent

    def cancelled_text(self) -> str:
        return " ".join(self.venue.cancels)

    def fail_cancel(self) -> None:
        self.venue.cancel_fails = True

    def cancel_all_reached_venue(self) -> bool:
        return self.venue.cancelled_all

    def new_own_trade(self) -> str:
        self.venue.fills.append(
            Fill("nk", "F1", "B2", "buy", D("0.0072"), D("1"), D("0.00001"), "BTC", NOW)
        )
        return "F1"

    def orders_gone(self) -> None:
        self.venue.active = []

    def blank_deposit(self) -> None:
        self.venue.address = " "

    def requests(self) -> int:
        return self.venue.calls + self.read_only.calls


# --- one broken variant per clause ---------------------------------------------


class NoResync(FakeVenue):
    resync = None  # type: ignore[assignment]


class WrongQuote(FakeVenue):
    async def market(self) -> MarketSpec:
        return replace(await super().market(), quote="USDT")


class LeakyParser(FakeVenue):
    async def market(self) -> MarketSpec:
        if self.market_payload is None:
            raise KeyError("symbol")
        return await super().market()


class UnsortedBook(FakeVenue):
    async def book_snapshot(self, depth: int) -> BookSnapshot:
        book = await super().book_snapshot(depth)
        return replace(book, bids=tuple(reversed(book.bids)))


class NewestFirstTrades(FakeVenue):
    async def recent_trades(self, limit: int) -> list[Trade]:
        return list(reversed(await super().recent_trades(limit)))


class NoMinuteCandles(FakeVenue):
    async def candles(self, tf: Any, limit: int) -> list[Candle]:
        return [] if tf == "1m" else await super().candles(tf, limit)


class SilentStream(FakeVenue):
    async def market_stream(self) -> AsyncIterator[Any]:
        async for event in super().market_stream():
            if not isinstance(event, ConnState):
                yield event


class DeltaFirst(FakeVenue):
    async def market_stream(self) -> AsyncIterator[Any]:
        yield BookDelta("nk", (), (), NOW, seq=11)
        async for event in super().market_stream():
            yield event


class RepeatsTrades(FakeVenue):
    async def market_stream(self) -> AsyncIterator[Any]:
        async for event in super().market_stream():
            yield event
            if isinstance(event, ConnState) and event.status == "reconnecting":
                for trade in TRADES:
                    yield trade


class NoFallback(FakeVenue):
    async def market_stream(self) -> AsyncIterator[Any]:
        if self.ws == "dead":
            yield self.state("reconnecting")
            yield self.book()
            return
        async for event in super().market_stream():
            yield event


class LeaksTask(FakeVenue):
    async def _spin(self) -> None:
        await asyncio.sleep(60)

    async def market_stream(self) -> AsyncIterator[Any]:
        self.leak = asyncio.create_task(self._spin(), name="venue-leaked-market_stream")
        async for event in super().market_stream():
            yield event


class DeafResync(FakeVenue):
    async def resync(self) -> None:
        pass


class NoSkew(FakeVenue):
    async def market(self) -> MarketSpec:
        spec = await super().market()
        self.clock_skew_ms = None
        return spec


class OpenWithoutKeys(FakeVenue):
    async def recent_orders(self, since: int) -> list[Order]:
        await self.hit("private")
        return []


class NoLockedBalance(FakeVenue):
    async def balances(self) -> dict[str, Balance]:
        balances = await super().balances()
        return balances | {"BTC": Balance("BTC", balances["BTC"].available)}


class ReportsDeadOrders(FakeVenue):
    async def open_orders(self) -> list[Order]:
        dead = self.order("buy", "1", "0.007", "Z9", "cancelled")
        return [*await super().open_orders(), dead]


class LosesClientId(FakeVenue):
    async def place_limit(self, order_request: OrderRequest) -> Order:
        return replace(await super().place_limit(order_request), client_id=None)


class FloatOnTheWire(FakeVenue):
    async def place_limit(self, order_request: OrderRequest) -> Order:
        order = await super().place_limit(order_request)
        self.sent = f"{float(order_request.price) * 1.0000001} {float(order_request.qty)!r:.6}"
        return order


class RefusedIsUncertain(FakeVenue):
    async def place_limit(self, order_request: OrderRequest) -> Order:
        try:
            return await super().place_limit(order_request)
        except httpx.ConnectError as error:
            raise UncertainError("fake: incierta") from error


class UncertainIsPlainError(FakeVenue):
    async def place_limit(self, order_request: OrderRequest) -> Order:
        try:
            return await super().place_limit(order_request)
        except UncertainError as error:
            raise VenueError(str(error)) from error


class CancelGoesNowhere(FakeVenue):
    async def cancel(self, order_id: str) -> None:
        await self.hit("cancel")


class SwallowsCancelFailure(FakeVenue):
    async def cancel(self, order_id: str) -> None:
        try:
            await super().cancel(order_id)
        except VenueError:
            pass


class CancelAllDoesNothing(FakeVenue):
    async def cancel_all(self) -> None:
        pass


class RepeatsFill(FakeVenue):
    async def private_stream(self) -> AsyncIterator[Any]:
        async for event in super().private_stream():
            yield event
            if isinstance(event, Fill):
                yield event


class ForgetsGoneOrder(FakeVenue):
    async def private_stream(self) -> AsyncIterator[Any]:
        async for event in super().private_stream():
            if not isinstance(event, Order):
                yield event


class MissesKnownOrder(FakeVenue):
    async def recent_orders(self, since: int) -> list[Order]:
        await super().recent_orders(since)
        return []


class SkipsLimiter(FakeVenue):
    async def book_snapshot(self, depth: int) -> BookSnapshot:
        self.calls += 1
        return self.book()


class KeepsPlainSecret(FakeVenue):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.api_secret = SECRET


class AcceptsBlankAddress(FakeVenue):
    async def deposit_address(self, asset: str) -> DepositAddress:
        return DepositAddress("nk", asset, asset, "main", self.address or "x")


class FeeForAnyAsset(FakeVenue):
    async def withdrawal_fee(self, asset: str) -> WithdrawalFee:
        return WithdrawalFee("nk", asset, asset, D("0.0001"))


class ZeroTokenPrice(FakeVenue):
    async def token_price(self, asset: str) -> Any:
        return D("0")


MUTANTS: dict[str, type[FakeVenue]] = {
    "satisfies_protocols": NoResync,
    "market_rules": WrongQuote,
    "market_garbage_is_venue_error": LeakyParser,
    "book_is_ordered": UnsortedBook,
    "trades_are_chronological": NewestFirstTrades,
    "candles_every_timeframe": NoMinuteCandles,
    "stream_reports_connection": SilentStream,
    "stream_book_chains": DeltaFirst,
    "stream_repeats_no_trade": RepeatsTrades,
    "stream_falls_back_to_rest": NoFallback,
    "stream_closes_cleanly": LeaksTask,
    "resync_brings_a_snapshot": DeafResync,
    "clock_skew_from_date": NoSkew,
    "private_needs_keys": OpenWithoutKeys,
    "balances_cover_the_pair": NoLockedBalance,
    "open_orders_are_live": ReportsDeadOrders,
    "place_limit_echoes_the_request": LosesClientId,
    "place_limit_sends_exact_numbers": FloatOnTheWire,
    "refused_send_is_not_uncertain": RefusedIsUncertain,
    "unclear_send_is_uncertain": UncertainIsPlainError,
    "cancel_reaches_the_venue": CancelGoesNowhere,
    "cancel_failure_raises": SwallowsCancelFailure,
    "cancel_all_reaches_the_venue": CancelAllDoesNothing,
    "private_stream_reports_new_fill_once": RepeatsFill,
    "private_stream_resolves_gone_order": ForgetsGoneOrder,
    "recent_orders_find_a_known_order": MissesKnownOrder,
    "requests_pass_the_limiter": SkipsLimiter,
    "secrets_stay_out": KeepsPlainSecret,
    "deposit_address": AcceptsBlankAddress,
    "withdrawal_fee": FeeForAnyAsset,
    "token_price": ZeroTokenPrice,
}
