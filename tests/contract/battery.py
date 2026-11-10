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
    def register(check_function: Callable[[Harness], Awaitable[None]]):
        CHECKS[check_function.__name__] = Check(check_function.__name__, check_function, needs)
        return check_function

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
    return [event for event in events if isinstance(event, kind)]


# --- shape and public data -----------------------------------------------------


@check
async def satisfies_protocols(harness: Harness) -> None:
    assert isinstance(harness.venue, Venue), "no cumple Venue"
    assert isinstance(harness.venue, FeedUpkeep), "no cumple FeedUpkeep"
    assert isinstance(harness.read_only, MarketData), "sin keys no cumple MarketData"
    assert isinstance(harness.venue.keeps_client_id, bool)
    for optional in (Depositor, WithdrawalFees, TokenPricer):
        assert isinstance(harness.venue, optional) == (optional in harness.implements), optional


@check
async def market_rules(harness: Harness) -> None:
    spec = await harness.venue.market()
    assert isinstance(spec, MarketSpec)
    assert spec.venue == harness.venue.id
    assert spec.quote == "BTC", "RNF-10: solo pares contra BTC"
    assert spec.base != spec.quote
    assert spec.min_qty >= spec.lot_size or spec.min_qty == 0
    assert spec.min_notional >= 0
    assert 0 <= spec.maker_fee < 1 and 0 <= spec.taker_fee < 1


@check
async def market_garbage_is_venue_error(harness: Harness) -> None:
    harness.garble_market()
    try:
        await harness.venue.market()
    except VenueError:
        return
    except Exception as error:
        raise AssertionError(f"un cambio de API debe ser VenueError, no {error!r}") from error
    raise AssertionError("aceptó un mercado ilegible")


@check
async def book_is_ordered(harness: Harness) -> None:
    book = await harness.venue.book_snapshot(50)
    assert isinstance(book, BookSnapshot)
    assert book.venue == harness.venue.id
    assert book.bids and book.asks
    bids, asks = (
        [price_level.price for price_level in book.bids],
        [price_level.price for price_level in book.asks],
    )
    assert bids == sorted(set(bids), reverse=True), "bids: de mejor a peor, sin repetir"
    assert asks == sorted(set(asks)), "asks: de mejor a peor, sin repetir"
    assert all(
        price_level.price > 0 and price_level.qty > 0 for price_level in (*book.bids, *book.asks)
    )
    assert bids[0] < asks[0], "book cruzado"
    assert book.ts >= EPOCH_MS_2020


@check
async def trades_are_chronological(harness: Harness) -> None:
    trades = await harness.venue.recent_trades(50)
    assert trades and all(isinstance(trade, Trade) for trade in trades)
    assert [trade.ts for trade in trades] == sorted(trade.ts for trade in trades)
    for trade in trades:
        assert trade.venue == harness.venue.id
        assert trade.side in ("buy", "sell") and trade.price > 0 and trade.qty > 0
        assert trade.ts >= EPOCH_MS_2020


@check
async def candles_every_timeframe(harness: Harness) -> None:
    for tf in get_args(Timeframe):
        candles = await harness.venue.candles(tf, 5)
        assert candles, tf
        assert all(isinstance(candle, Candle) for candle in candles)
        opens = [candle.ts_open for candle in candles]
        assert opens == sorted(set(opens)), f"{tf}: de vieja a nueva"
        assert opens[0] >= EPOCH_MS_2020
        for candle in candles:
            assert candle.low <= min(candle.open, candle.close) and candle.high >= max(
                candle.open, candle.close
            ), candle
            assert candle.volume >= 0




def outside(task: asyncio.Task[Any]) -> bool:
    """The local WS server of the harness runs in this same loop: not the adapter's."""
    code = getattr(task.get_coro(), "cr_code", None)
    path = code.co_filename if code else ""
    return "websockets" in path or path.endswith("contract/venues.py")


@check
async def stream_reports_connection(harness: Harness) -> None:
    async with harness.stream_venue() as venue, aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))
    states = of_type(ConnState, events)
    assert any(state.status == "up" and state.channel == "market" for state in states), events
    assert all(state.venue == venue.id for state in states)


@check
async def stream_book_chains(harness: Harness) -> None:
    """A snapshot first, then consecutive deltas: `L2Book` rejects anything else."""
    async with harness.stream_venue() as venue, aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, lambda ev: bool(of_type(Trade, ev)))
        events += await read_on(stream)
    books = [event for event in events if isinstance(event, BookSnapshot | BookDelta)]
    assert isinstance(books[0], BookSnapshot), "un delta antes del snapshot"
    seq = books[0].seq
    for book in books[1:]:
        if isinstance(book, BookSnapshot):
            seq = book.seq
        elif seq is not None:
            assert book.seq == seq + 1, f"delta {book.seq} tras {seq}"
            seq = book.seq


@check
async def stream_repeats_no_trade(harness: Harness) -> None:
    def second_session(events: list[Any]) -> bool:
        return len(of_type(BookSnapshot, events)) >= 2

    async with harness.stream_venue(drop=True) as venue, aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, second_session)
        events += await read_on(stream)
    trades = of_type(Trade, events)
    assert trades and all(trade.venue == venue.id for trade in trades)
    assert len(trades) == len(set(trades)), "una reconexión repitió trades"


@check
async def stream_falls_back_to_rest(harness: Harness) -> None:
    venue = harness.dead_stream_venue()

    def on_rest(events: list[Any]) -> bool:
        return any(isinstance(event, BookSnapshot) for event in events)

    async with aclosing(venue.market_stream()) as stream:
        events = await read_until(stream, on_rest)
    states = of_type(ConnState, events)
    assert any(state.status != "up" for state in states), "no avisó de la caída"
    assert any(state.status == "up" and state.source == "rest" for state in states), states


@check
async def stream_closes_cleanly(harness: Harness) -> None:
    before = asyncio.all_tasks()
    async with harness.stream_venue() as venue:
        async with aclosing(venue.market_stream()) as stream:
            await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))
        await asyncio.sleep(0.01)
        left = [
            task for task in asyncio.all_tasks() - before if not task.done() and not outside(task)
        ]
        assert not left, f"tareas vivas tras cerrar el stream: {left}"


@check
async def resync_brings_a_snapshot(harness: Harness) -> None:
    async with harness.stream_venue() as venue, aclosing(venue.market_stream()) as stream:
        await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))
        await venue.resync()
        await read_until(stream, lambda ev: bool(of_type(BookSnapshot, ev)))


@check
async def clock_skew_from_date(harness: Harness) -> None:
    now = harness.venue_clock()
    harness.date_market(now + SKEW_MS)
    await harness.venue.market()
    skew = harness.venue.clock_skew_ms
    assert skew is not None, "no midió el desfase"
    assert abs(skew - SKEW_MS) <= 1_000, skew




@check
async def private_needs_keys(harness: Harness) -> None:
    read_only_venue = harness.read_only
    calls: list[Callable[[], Awaitable[Any]]] = [
        read_only_venue.balances,
        read_only_venue.open_orders,
        lambda: read_only_venue.place_limit(harness.order),
        lambda: read_only_venue.cancel("x"),
        read_only_venue.cancel_all,
        lambda: read_only_venue.recent_orders(0),
        lambda: anext(read_only_venue.private_stream()),
    ]
    before = harness.requests()
    for call in calls:
        try:
            await call()
        except ReadOnlyError:
            continue
        raise AssertionError(f"sin keys no lanzó ReadOnlyError: {call}")
    assert harness.requests() == before, "sin keys, una llamada llegó al venue"


@check
async def balances_cover_the_pair(harness: Harness) -> None:
    spec = await harness.venue.market()
    balances = await harness.venue.balances()
    assert {spec.base, spec.quote} <= set(balances)
    for asset, balance in balances.items():
        assert isinstance(balance, Balance) and balance.asset == asset
        assert balance.available >= 0 and balance.locked >= 0
    assert balances["BTC"].locked == harness.locked_btc, "lo que está en órdenes va en locked"


@check
async def open_orders_are_live(harness: Harness) -> None:
    orders = await harness.venue.open_orders()
    assert orders and all(isinstance(order, Order) for order in orders)
    assert all(order.venue == harness.venue.id for order in orders)
    assert {order.status for order in orders} <= LIVE_STATUSES
    assert not {order.order_id for order in orders} & set(harness.foreign_orders), (
        "orden de otro par"
    )


@check
async def place_limit_echoes_the_request(harness: Harness) -> None:
    order_request = harness.order
    order = await harness.venue.place_limit(order_request)
    assert isinstance(order, Order)
    assert order.order_id
    assert (order.venue, order.side) == (order_request.venue, order_request.side)
    assert (order.price, order.qty) == (order_request.price, order_request.qty)
    assert order.client_id == order_request.client_id, "la orden devuelta pierde su client_id"


@check
async def place_limit_sends_exact_numbers(harness: Harness) -> None:
    order_request = replace(harness.order, price=D("0.00725001"), qty=D("0.12345678"))
    await harness.venue.place_limit(order_request)
    text = harness.sent_text()
    assert "0.00725001" in text and "0.12345678" in text, text


@check
async def refused_send_is_not_uncertain(harness: Harness) -> None:
    for outcome in ("rejected", "refused"):
        harness.send_outcome(outcome)
        try:
            await harness.venue.place_limit(harness.order)
        except UncertainError as error:
            raise AssertionError(f"{outcome}: la orden no existe, no es incierta") from error
        except (VenueError, httpx.HTTPError):
            continue
        raise AssertionError(f"{outcome}: no lanzó error")


@check
async def unclear_send_is_uncertain(harness: Harness) -> None:
    for outcome in UNCLEAR_OUTCOMES:
        harness.send_outcome(outcome)
        try:
            await harness.venue.place_limit(harness.order)
        except UncertainError:
            continue
        except Exception as error:
            raise AssertionError(f"{outcome}: debe ser UncertainError, no {error!r}") from error
        raise AssertionError(f"{outcome}: no lanzó error")


@check
async def cancel_reaches_the_venue(harness: Harness) -> None:
    await harness.venue.cancel("order-42")
    assert "order-42" in harness.cancelled_text()


@check
async def cancel_failure_raises(harness: Harness) -> None:
    harness.fail_cancel()
    try:
        await harness.venue.cancel("order-42")
    except VenueError:
        return
    raise AssertionError("un cancel fallido pasó por bueno")


@check
async def cancel_all_reaches_the_venue(harness: Harness) -> None:
    await harness.venue.cancel_all()
    assert harness.cancel_all_reached_venue()


def private_up(events: list[Any]) -> bool:
    return any(isinstance(event, ConnState) and event.status == "up" for event in events)


@check
async def private_stream_reports_new_fill_once(harness: Harness) -> None:
    async with aclosing(harness.venue.private_stream()) as stream:
        seen = await read_until(stream, private_up)
        assert all(event.channel == "private" for event in of_type(ConnState, seen))
        # Trades of the first refresh are history, not new fills
        await asyncio.sleep(0.05)
        trade_id = harness.new_own_trade()

        def filled(events: list[Any]) -> bool:
            return any(isinstance(event, Fill) and event.trade_id == trade_id for event in events)

        events = await read_until(stream, filled)
        events += await read_on(stream)
    fills = [
        check_function
        for check_function in of_type(Fill, seen + events)
        if check_function.trade_id == trade_id
    ]
    assert len(fills) == 1, f"el fill salió {len(fills)} veces"
    assert fills[0].venue == harness.venue.id and isinstance(fills[0].fee, Decimal)
    assert not [
        check_function
        for check_function in of_type(Fill, seen + events)
        if check_function.trade_id != trade_id
    ], "historia"


@check
async def private_stream_resolves_gone_order(harness: Harness) -> None:
    def resolved(events: list[Any]) -> bool:
        orders = of_type(Order, events)
        return any(order.order_id == harness.gone_order and order.is_done for order in orders)

    async with aclosing(harness.venue.private_stream()) as stream:
        await read_until(stream, private_up)
        await asyncio.sleep(0.05)
        harness.orders_gone()
        await read_until(stream, resolved)


@check
async def recent_orders_find_a_known_order(harness: Harness) -> None:
    orders = await harness.venue.recent_orders(0)
    assert all(isinstance(order, Order) for order in orders)
    want = harness.known
    if harness.venue.keeps_client_id:
        assert any(order.client_id == want.client_id for order in orders), "sin client_id"
    else:
        same = [(order.side, order.price, order.qty) for order in orders]
        assert (want.side, want.price, want.qty) in same, same


@check
async def requests_pass_the_limiter(harness: Harness) -> None:
    await harness.venue.market()
    await harness.venue.book_snapshot(5)
    await harness.venue.balances()
    await harness.venue.place_limit(harness.order)
    await harness.venue.cancel("order-42")
    assert harness.limiter.kinds.count("order") == 1
    assert harness.limiter.kinds.count("cancel") == 1
    assert len(harness.limiter.kinds) >= harness.requests() > 0, (
        "una petición se saltó el limitador"
    )


@check
async def secrets_stay_out(harness: Harness) -> None:
    harness.send_outcome("rejected")
    texts = [repr(vars(harness.venue))]
    try:
        await harness.venue.place_limit(harness.order)
    except VenueError as error:
        texts.append(repr(error))
    for secret in harness.secrets:
        assert not any(secret in text for text in texts), "un secreto en un texto"




@check(needs=Depositor)
async def deposit_address(harness: Harness) -> None:
    spec = await harness.venue.market()
    for asset in (spec.base, spec.quote):
        got = await harness.venue.deposit_address(asset)
        assert isinstance(got, DepositAddress)
        assert (got.venue, got.asset) == (harness.venue.id, asset)
        assert got.address.strip() and got.ticker, got
    await must_fail(harness.venue.deposit_address("DOGE"), VenueError)
    await must_fail(harness.read_only.deposit_address(spec.base), ReadOnlyError)
    harness.blank_deposit()
    await must_fail(harness.venue.deposit_address(spec.base), VenueError)


@check(needs=WithdrawalFees)
async def withdrawal_fee(harness: Harness) -> None:
    spec = await harness.venue.market()
    for asset in (spec.base, spec.quote):
        got = await harness.venue.withdrawal_fee(asset)
        assert isinstance(got, WithdrawalFee)
        assert (got.venue, got.asset) == (harness.venue.id, asset)
        assert got.fee >= 0 and got.ticker
    await must_fail(harness.venue.withdrawal_fee("DOGE"), VenueError)


@check(needs=TokenPricer)
async def token_price(harness: Harness) -> None:
    price = await harness.venue.token_price(harness.token)
    assert isinstance(price, Decimal) and price > 0


async def must_fail(call: Awaitable[Any], error: type[Exception]) -> None:
    try:
        await call
    except error:
        return
    raise AssertionError(f"debía lanzar {error.__name__}")
