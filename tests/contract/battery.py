"""The venue contract as checks (RF-36, D-82). One check per clause of
docs/contrato-venue.md: a test keeps both lists equal, and another one requires a
broken adapter per check, so a check that cannot fail does not survive here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, get_args

import httpx

from xbt_terminal.adapters.base import (
    Depositor,
    FeedUpkeep,
    MarketData,
    ReadOnlyError,
    TokenPricer,
    UncertainError,
    Venue,
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
    MarketSpec,
    Order,
    Timeframe,
    Trade,
    WithdrawalFee,
)

from .harness import Harness

EPOCH_MS_2020 = 1_577_836_800_000  # below this, a timestamp is not in ms
SKEW_MS = 5_000
LIVE_STATUSES = {"open", "partial"}
UNCLEAR_OUTCOMES = ("server_error", "timeout", "not_json", "unreadable")


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[Harness], Awaitable[None]]
    needs: type | None = None


CHECKS: dict[str, Check] = {}


def check(fn: Callable[[Harness], Awaitable[None]] | None = None, *, needs: type | None = None):
    def register(f: Callable[[Harness], Awaitable[None]]):
        CHECKS[f.__name__] = Check(f.__name__, f, needs)
        return f

    return register(fn) if fn is not None else register


async def read_until(stream: AsyncIterator[Any], done: Callable[[list[Any]], bool]) -> list[Any]:
    """Events until `done(events)`, or a clause failure if they do not arrive."""
    events: list[Any] = []
    try:
        async with asyncio.timeout(3.0):
            async for event in stream:
                events.append(event)
                if done(events):
                    return events
    except TimeoutError:
        pass
    raise AssertionError(f"the stream never delivered what was expected: {events}")


async def read_on(stream: AsyncIterator[Any], seconds: float = 0.1) -> list[Any]:
    """Whatever arrives during `seconds`. It cancels the generator: last read of a stream."""
    events: list[Any] = []
    try:
        async with asyncio.timeout(seconds):
            async for event in stream:
                events.append(event)
    except TimeoutError:
        pass
    return events


def of_type(kind: type, events: list[Any]) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


# --- shape and public data -----------------------------------------------------


@check
async def satisfies_protocols(h: Harness) -> None:
    assert isinstance(h.venue, Venue), "no cumple Venue"
    assert isinstance(h.venue, FeedUpkeep), "no cumple FeedUpkeep"
    assert isinstance(h.read_only, MarketData), "sin keys no cumple MarketData"
    assert isinstance(h.venue.keeps_client_id, bool)
    for optional in (Depositor, WithdrawalFees, TokenPricer):
        assert isinstance(h.venue, optional) == (optional in h.implements), optional


@check
async def market_rules(h: Harness) -> None:
    spec = await h.venue.market()
    assert isinstance(spec, MarketSpec)
    assert spec.venue == h.venue.id
    assert spec.quote == "BTC", "RNF-10: solo pares contra BTC"
    assert spec.base != spec.quote
    assert spec.min_qty >= spec.lot_size or spec.min_qty == 0
    assert spec.min_notional >= 0
    assert 0 <= spec.maker_fee < 1 and 0 <= spec.taker_fee < 1


@check
async def market_garbage_is_venue_error(h: Harness) -> None:
    h.garble_market()
    try:
        await h.venue.market()
    except VenueError:
        return
    except Exception as exc:
        raise AssertionError(f"un cambio de API debe ser VenueError, no {exc!r}") from exc
    raise AssertionError("aceptó un mercado ilegible")


@check
async def book_is_ordered(h: Harness) -> None:
    book = await h.venue.book_snapshot(50)
    assert isinstance(book, BookSnapshot)
    assert book.venue == h.venue.id
    assert book.bids and book.asks
    bids, asks = [lv.price for lv in book.bids], [lv.price for lv in book.asks]
    assert bids == sorted(set(bids), reverse=True), "bids: de mejor a peor, sin repetir"
    assert asks == sorted(set(asks)), "asks: de mejor a peor, sin repetir"
    assert all(lv.price > 0 and lv.qty > 0 for lv in (*book.bids, *book.asks))
    assert bids[0] < asks[0], "book cruzado"
    assert book.ts >= EPOCH_MS_2020


@check
async def trades_are_chronological(h: Harness) -> None:
    trades = await h.venue.recent_trades(50)
    assert trades and all(isinstance(t, Trade) for t in trades)
    assert [t.ts for t in trades] == sorted(t.ts for t in trades)
    for t in trades:
        assert t.venue == h.venue.id
        assert t.side in ("buy", "sell") and t.price > 0 and t.qty > 0
        assert t.ts >= EPOCH_MS_2020


@check
async def candles_every_timeframe(h: Harness) -> None:
    for tf in get_args(Timeframe):
        candles = await h.venue.candles(tf, 5)
        assert candles, tf
        assert all(isinstance(c, Candle) for c in candles)
        opens = [c.ts_open for c in candles]
        assert opens == sorted(set(opens)), f"{tf}: de vieja a nueva"
        assert opens[0] >= EPOCH_MS_2020
        for c in candles:
            assert c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close), c
            assert c.volume >= 0


# --- market stream -------------------------------------------------------------


def outside(task: asyncio.Task[Any]) -> bool:
    """The local WS server of the harness runs in this same loop: not the adapter's."""
    code = getattr(task.get_coro(), "cr_code", None)
    path = code.co_filename if code else ""
    return "websockets" in path or path.endswith("contract/venues.py")


@check
async def stream_reports_connection(h: Harness) -> None:
    async with h.stream_venue() as venue, aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))
    states = of_type(ConnState, events)
    assert any(s.status == "up" and s.channel == "market" for s in states), events
    assert all(s.venue == venue.id for s in states)


@check
async def stream_book_chains(h: Harness) -> None:
    """A snapshot first, then consecutive deltas: `L2Book` rejects anything else."""
    async with h.stream_venue() as venue, aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, lambda ev: bool(of_type(Trade, ev)))
        events += await read_on(stream)
    books = [e for e in events if isinstance(e, BookSnapshot | BookDelta)]
    assert isinstance(books[0], BookSnapshot), "un delta antes del snapshot"
    seq = books[0].seq
    for book in books[1:]:
        if isinstance(book, BookSnapshot):
            seq = book.seq
        elif seq is not None:
            assert book.seq == seq + 1, f"delta {book.seq} tras {seq}"
            seq = book.seq


@check
async def stream_repeats_no_trade(h: Harness) -> None:
    def second_session(events: list[Any]) -> bool:
        return len(of_type(BookSnapshot, events)) >= 2

    async with h.stream_venue(drop=True) as venue, aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, second_session)
        events += await read_on(stream)
    trades = of_type(Trade, events)
    assert trades and all(t.venue == venue.id for t in trades)
    assert len(trades) == len(set(trades)), "una reconexión repitió trades"


@check
async def stream_falls_back_to_rest(h: Harness) -> None:
    venue = h.dead_stream_venue()

    def on_rest(events: list[Any]) -> bool:
        return any(isinstance(e, BookSnapshot) for e in events)

    async with aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, on_rest)
    states = of_type(ConnState, events)
    assert any(s.status != "up" for s in states), "no avisó de la caída"
    assert any(s.status == "up" and s.source == "rest" for s in states), states


@check
async def stream_closes_cleanly(h: Harness) -> None:
    before = asyncio.all_tasks()
    async with h.stream_venue() as venue:
        async with aclosing(venue.market_stream()) as stream:
            await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))
        await asyncio.sleep(0.01)
        left = [t for t in asyncio.all_tasks() - before if not t.done() and not outside(t)]
        assert not left, f"tareas vivas tras cerrar el stream: {left}"


@check
async def resync_brings_a_snapshot(h: Harness) -> None:
    async with h.stream_venue() as venue, aclosing(venue.market_stream()) as stream:
        await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))
        await venue.resync()
        await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))


@check
async def clock_skew_from_date(h: Harness) -> None:
    now = h.venue_clock()
    h.date_market(now + SKEW_MS)
    await h.venue.market()
    skew = h.venue.clock_skew_ms
    assert skew is not None, "no midió el desfase"
    assert abs(skew - SKEW_MS) <= 1_000, skew


# --- private -------------------------------------------------------------------


@check
async def private_needs_keys(h: Harness) -> None:
    v = h.read_only
    calls: list[Callable[[], Awaitable[Any]]] = [
        v.balances,
        v.open_orders,
        lambda: v.place_limit(h.order),
        lambda: v.cancel("x"),
        v.cancel_all,
        lambda: v.recent_orders(0),
        lambda: anext(v.private_stream()),
    ]
    before = h.requests()
    for call in calls:
        try:
            await call()
        except ReadOnlyError:
            continue
        raise AssertionError(f"sin keys no lanzó ReadOnlyError: {call}")
    assert h.requests() == before, "sin keys, una llamada llegó al venue"


@check
async def balances_cover_the_pair(h: Harness) -> None:
    spec = await h.venue.market()
    balances = await h.venue.balances()
    assert {spec.base, spec.quote} <= set(balances)
    for asset, b in balances.items():
        assert isinstance(b, Balance) and b.asset == asset
        assert b.available >= 0 and b.locked >= 0
    assert balances["BTC"].locked == h.locked_btc, "lo que está en órdenes va en locked"


@check
async def open_orders_are_live(h: Harness) -> None:
    orders = await h.venue.open_orders()
    assert orders and all(isinstance(o, Order) for o in orders)
    assert all(o.venue == h.venue.id for o in orders)
    assert {o.status for o in orders} <= LIVE_STATUSES
    assert not {o.order_id for o in orders} & set(h.foreign_orders), "orden de otro par"


@check
async def place_limit_echoes_the_request(h: Harness) -> None:
    req = h.order
    order = await h.venue.place_limit(req)
    assert isinstance(order, Order)
    assert order.order_id
    assert (order.venue, order.side) == (req.venue, req.side)
    assert (order.price, order.qty) == (req.price, req.qty)
    assert order.client_id == req.client_id, "la orden devuelta pierde su client_id"


@check
async def place_limit_sends_exact_numbers(h: Harness) -> None:
    req = replace(h.order, price=D("0.00725001"), qty=D("0.12345678"))
    await h.venue.place_limit(req)
    text = h.sent_text()
    assert "0.00725001" in text and "0.12345678" in text, text


@check
async def refused_send_is_not_uncertain(h: Harness) -> None:
    for outcome in ("rejected", "refused"):
        h.send_outcome(outcome)
        try:
            await h.venue.place_limit(h.order)
        except UncertainError as exc:
            raise AssertionError(f"{outcome}: la orden no existe, no es incierta") from exc
        except (VenueError, httpx.HTTPError):
            continue
        raise AssertionError(f"{outcome}: no lanzó error")


@check
async def unclear_send_is_uncertain(h: Harness) -> None:
    for outcome in UNCLEAR_OUTCOMES:
        h.send_outcome(outcome)
        try:
            await h.venue.place_limit(h.order)
        except UncertainError:
            continue
        except Exception as exc:
            raise AssertionError(f"{outcome}: debe ser UncertainError, no {exc!r}") from exc
        raise AssertionError(f"{outcome}: no lanzó error")


@check
async def cancel_reaches_the_venue(h: Harness) -> None:
    await h.venue.cancel("order-42")
    assert "order-42" in h.cancelled_text()


@check
async def cancel_failure_raises(h: Harness) -> None:
    h.fail_cancel()
    try:
        await h.venue.cancel("order-42")
    except VenueError:
        return
    raise AssertionError("un cancel fallido pasó por bueno")


@check
async def cancel_all_reaches_the_venue(h: Harness) -> None:
    await h.venue.cancel_all()
    assert h.cancel_all_reached_venue()


def private_up(events: list[Any]) -> bool:
    return any(isinstance(e, ConnState) and e.status == "up" for e in events)


@check
async def private_stream_reports_new_fill_once(h: Harness) -> None:
    async with aclosing(h.venue.private_stream()) as stream:
        seen = await read_until(stream, private_up)
        assert all(e.channel == "private" for e in of_type(ConnState, seen))
        # Trades of the first refresh are history, not new fills
        await asyncio.sleep(0.05)
        trade_id = h.new_own_trade()

        def filled(events: list[Any]) -> bool:
            return any(isinstance(e, Fill) and e.trade_id == trade_id for e in events)

        events = await read_until(stream, filled)
        events += await read_on(stream)
    fills = [f for f in of_type(Fill, seen + events) if f.trade_id == trade_id]
    assert len(fills) == 1, f"el fill salió {len(fills)} veces"
    assert fills[0].venue == h.venue.id and isinstance(fills[0].fee, Decimal)
    assert not [f for f in of_type(Fill, seen + events) if f.trade_id != trade_id], "historia"


@check
async def private_stream_resolves_gone_order(h: Harness) -> None:
    def resolved(events: list[Any]) -> bool:
        orders = of_type(Order, events)
        return any(o.order_id == h.gone_order and o.is_done for o in orders)

    async with aclosing(h.venue.private_stream()) as stream:
        await read_until(stream, private_up)
        await asyncio.sleep(0.05)
        h.orders_gone()
        await read_until(stream, resolved)


@check
async def recent_orders_find_a_known_order(h: Harness) -> None:
    orders = await h.venue.recent_orders(0)
    assert all(isinstance(o, Order) for o in orders)
    want = h.known
    if h.venue.keeps_client_id:
        assert any(o.client_id == want.client_id for o in orders), "sin client_id"
    else:
        same = [(o.side, o.price, o.qty) for o in orders]
        assert (want.side, want.price, want.qty) in same, same


@check
async def requests_pass_the_limiter(h: Harness) -> None:
    await h.venue.market()
    await h.venue.book_snapshot(5)
    await h.venue.balances()
    await h.venue.place_limit(h.order)
    await h.venue.cancel("order-42")
    assert h.limiter.kinds.count("order") == 1
    assert h.limiter.kinds.count("cancel") == 1
    assert len(h.limiter.kinds) >= h.requests() > 0, "una petición se saltó el limitador"


@check
async def secrets_stay_out(h: Harness) -> None:
    h.send_outcome("rejected")
    texts = [repr(vars(h.venue))]
    try:
        await h.venue.place_limit(h.order)
    except VenueError as exc:
        texts.append(repr(exc))
    for secret in h.secrets:
        assert not any(secret in text for text in texts), "un secreto en un texto"


# --- optional ------------------------------------------------------------------


@check(needs=Depositor)
async def deposit_address(h: Harness) -> None:
    spec = await h.venue.market()
    for asset in (spec.base, spec.quote):
        got = await h.venue.deposit_address(asset)
        assert isinstance(got, DepositAddress)
        assert (got.venue, got.asset) == (h.venue.id, asset)
        assert got.address.strip() and got.ticker, got
    await must_fail(h.venue.deposit_address("DOGE"), VenueError)
    await must_fail(h.read_only.deposit_address(spec.base), ReadOnlyError)
    h.blank_deposit()
    await must_fail(h.venue.deposit_address(spec.base), VenueError)


@check(needs=WithdrawalFees)
async def withdrawal_fee(h: Harness) -> None:
    spec = await h.venue.market()
    for asset in (spec.base, spec.quote):
        got = await h.venue.withdrawal_fee(asset)
        assert isinstance(got, WithdrawalFee)
        assert (got.venue, got.asset) == (h.venue.id, asset)
        assert got.fee >= 0 and got.ticker
    await must_fail(h.venue.withdrawal_fee("DOGE"), VenueError)


@check(needs=TokenPricer)
async def token_price(h: Harness) -> None:
    price = await h.venue.token_price(h.token)
    assert isinstance(price, Decimal) and price > 0


async def must_fail(call: Awaitable[Any], error: type[Exception]) -> None:
    try:
        await call
    except error:
        return
    raise AssertionError(f"debía lanzar {error.__name__}")
