"""NonKYC's market_stream against a local JSON-RPC server replaying real frames
(tests/fixtures/nonkyc/ws_frames.json) and a respx simulated REST.

The 17 recorded `updateOrderbook` chain onto the snapshot (91658) and arrived out of
order (91663 before 91662, 91666 before 91665, 91673 before 91672): replayed in that
same order."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx
from websockets.asyncio.server import serve

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.adapters.nonkyc import REST_URL, NonKYCVenue, ws_message
from xbt_terminal.adapters.streaming import iso_ms
from xbt_terminal.core.book import L2Book
from xbt_terminal.core.models import BookDelta, BookSnapshot, ConnState, D, Level, Trade

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nonkyc"
FRAMES = json.loads((FIXTURES / "ws_frames.json").read_text(encoding="utf-8"))
UPDATES: list[str] = FRAMES["updateOrderbook"]
SEQ = 91658  # sequence of the recorded snapshot
LAST_SEQ = SEQ + len(UPDATES)


def update_book(seq: int, bids=(), asks=()) -> str:
    """Same format as the real updateOrderbook."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "updateOrderbook",
            "params": {
                "asks": [{"price": p, "quantity": q} for p, q in asks],
                "bids": [{"price": p, "quantity": q} for p, q in bids],
                "symbol": "BTCB2/BTC",
                "timestamp": "2026-10-20T15:30:00.000Z",
                "sequence": seq,
            },
        }
    )


LAST_TRADE = (
    FRAMES["updateTrades"]
    .replace("6ad78386d8baa7ac8047f1ab", "fin-del-guion")
    .replace('"quantity":"0.5095"', '"quantity":"0.7777"')
)
OTHER_SYMBOL = update_book(SEQ + 1).replace("BTCB2/BTC", "XMR/BTC")
_reset = json.loads(FRAMES["snapshotOrderbook"])
_reset["params"]["sequence"] = 5
RESET_SNAPSHOT = json.dumps(_reset)


class FakeNonKYC:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.received: list[list[dict]] = []

    async def handler(self, ws):
        script = self.scripts.pop(0) if self.scripts else "stay"
        got: list[dict] = []
        self.received.append(got)

        async def expect(method: str):
            msg = json.loads(await ws.recv())
            got.append(msg)
            assert msg["method"] == method, msg

        await expect("subscribeOrderbook")
        await expect("subscribeTrades")
        if script == "error":
            await ws.send(FRAMES["errorSymbol"])  # and leaves the connection open
            await ws.wait_closed()
            return
        if script == "silent":
            await ws.wait_closed()
            return
        await ws.send(FRAMES["snapshotOrderbook"])
        await ws.send(FRAMES["snapshotTrades"])
        await ws.send(OTHER_SYMBOL)
        if script == "gap":
            await ws.send(update_book(SEQ + 2, bids=[("0.0071", "1")]))  # SEQ + 1 is missing
            await expect("subscribeOrderbook")  # the adapter asks for a new snapshot
            await ws.send(FRAMES["snapshotOrderbook"])
        else:
            for frame in UPDATES:
                await ws.send(frame)
        if script == "resync":  # the book freezes and the venue restarts its sequence
            await ws.send(FRAMES["updateTrades"])
            await expect("subscribeOrderbook")  # asked by `resync()` (D-76)
            await ws.send(update_book(LAST_SEQ + 1, bids=[("0.0070", "1")]))  # previous
            await ws.send(RESET_SNAPSHOT)
            await ws.send(update_book(6, bids=[("0.0071", "2")]))
            await ws.send(LAST_TRADE)
            await ws.wait_closed()
            return
        if script == "dup":
            await ws.send(UPDATES[0])  # repeated: already applied
            await asyncio.sleep(0.2)  # more than reorder_wait_s
        await ws.send(FRAMES["updateTrades"])
        if script == "drop":
            return
        await ws.send(LAST_TRADE)
        await ws.wait_closed()


class FreeLimiter:
    async def acquire(self, kind: str) -> None:
        pass


@pytest.fixture
def rest():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{REST_URL}/market/orderbook").respond(
            text=(FIXTURES / "orderbook.json").read_text(encoding="utf-8")
        )
        mock.get(f"{REST_URL}/market/trades").respond(
            text=(FIXTURES / "trades.json").read_text(encoding="utf-8")
        )
        yield mock


def make_venue(http, port: int) -> NonKYCVenue:
    return NonKYCVenue(
        "BTCB2/BTC",
        FreeLimiter(),
        maker_fee=D("0.002"),
        taker_fee=D("0.002"),
        http=http,
        ws_url=f"ws://127.0.0.1:{port}",
        fallback_every_s=0.01,
        backoff_base_s=0.01,
        backoff_max_s=0.05,
        reorder_wait_s=0.05,
        clock=lambda: 1_792_509_100_000,
    )


async def collect(server, until, timeout: float = 3.0, on_event=None):
    """`on_event(venue, events)`, if given, is awaited after each event."""
    async with serve(server.handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        async with httpx.AsyncClient() as http:
            venue = make_venue(http, port)
            events = []
            stream = venue.market_stream()
            try:
                async with asyncio.timeout(timeout):
                    async for ev in stream:
                        events.append(ev)
                        if on_event is not None:
                            await on_event(venue, events)
                        if until(events):
                            return events
            finally:
                await stream.aclose()


def is_last(events) -> bool:
    """LAST_TRADE is the last of each script."""
    return any(isinstance(e, Trade) and e.qty == D("0.7777") for e in events)


def states(events):
    return [(e.source, e.status) for e in events if isinstance(e, ConnState)]


def book_events(events):
    return [e for e in events if isinstance(e, BookSnapshot | BookDelta)]


def expected_book() -> L2Book:
    """The book applying the recorded frames sorted by sequence, by hand."""
    book = L2Book("nk")
    snap = json.loads(FRAMES["snapshotOrderbook"])["params"]
    book.apply(
        BookSnapshot(
            "nk",
            tuple(Level(D(lv["price"]), D(lv["quantity"])) for lv in snap["bids"]),
            tuple(Level(D(lv["price"]), D(lv["quantity"])) for lv in snap["asks"]),
            ts=0,
            seq=SEQ,
        )
    )
    for p in sorted((json.loads(u)["params"] for u in UPDATES), key=lambda p: p["sequence"]):
        assert book.apply(
            BookDelta(
                "nk",
                tuple(Level(D(str(lv["price"])), D(str(lv["quantity"]))) for lv in p["bids"]),
                tuple(Level(D(str(lv["price"])), D(str(lv["quantity"]))) for lv in p["asks"]),
                ts=0,
                seq=p["sequence"],
            )
        )
    return book


@pytest.mark.spec("RF-01", "RF-02")
class TestWebSocket:
    async def test_subscription_and_snapshot(self, rest):
        server = FakeNonKYC("stay")
        events = await collect(server, is_last)

        assert server.received[0] == [
            {"method": "subscribeOrderbook", "params": {"symbol": "BTCB2/BTC"}, "id": 1},
            {"method": "subscribeTrades", "params": {"symbol": "BTCB2/BTC"}, "id": 2},
        ]
        assert isinstance(events[0], ConnState) and states(events) == [("ws", "up")]
        snap = events[1]
        assert isinstance(snap, BookSnapshot)
        assert (snap.venue, snap.seq) == ("nk", SEQ)
        assert snap.ts == json.loads(FRAMES["snapshotOrderbook"])["timestamp"]

    async def test_unordered_deltas_get_reordered(self, rest):
        server = FakeNonKYC("stay")
        events = await collect(server, is_last)
        deltas = [e for e in events if isinstance(e, BookDelta)]
        assert [d.seq for d in deltas] == list(range(SEQ + 1, LAST_SEQ + 1))
        assert deltas[0].ts == iso_ms("2026-10-20T15:14:43.717Z")
        assert deltas[0].asks == (Level(D("0.00724756"), D("0")),)  # quantity: numeric 0
        assert len(server.received[0]) == 2  # no resubscriptions: there was no gap

    async def test_book_ends_same_as_applying_in_order(self, rest):
        server = FakeNonKYC("stay")
        events = await collect(server, is_last)
        book = L2Book("nk")
        assert all(book.apply(e) for e in book_events(events))
        want = expected_book()
        assert book.top(100) == want.top(100)
        assert book.seq == LAST_SEQ and book.mid is not None

    async def test_other_symbol_ignored(self, rest):
        events = await collect(FakeNonKYC("stay"), is_last)
        assert all(e.venue == "nk" for e in events)
        assert len([e for e in events if isinstance(e, BookDelta)]) == len(UPDATES)

    async def test_trades_listed(self, rest):
        events = await collect(FakeNonKYC("stay"), is_last)
        trades = [e for e in events if isinstance(e, Trade)]
        assert len(trades) == 52  # 50 of the snapshot + the real live one + the ending one
        assert [t.ts for t in trades[:50]] == sorted(t.ts for t in trades[:50])
        assert trades[50] == Trade("nk", D("0.00724378"), D("0.5095"), "sell", 1792508806091)

    async def test_repeated_delta_ignored(self, rest):
        server = FakeNonKYC("dup")
        events = await collect(server, is_last)
        deltas = [e for e in events if isinstance(e, BookDelta)]
        assert [d.seq for d in deltas] == list(range(SEQ + 1, LAST_SEQ + 1))
        assert len(server.received[0]) == 2  # not taken for a gap

    async def test_real_gap_asks_new_snapshot(self, rest):
        server = FakeNonKYC("gap")
        events = await collect(server, is_last)
        assert [m["method"] for m in server.received[0]] == [
            "subscribeOrderbook",
            "subscribeTrades",
            "subscribeOrderbook",
        ]
        book = L2Book("nk")
        valid = [book.apply(e) for e in book_events(events)]
        assert valid == [True, False, True]  # the gap invalidates, the snapshot repairs
        assert book.valid and book.seq == SEQ

    @pytest.mark.spec("RNF-24")
    async def test_resync_asks_book_on_same_ws(self, rest):
        """D-76: without reconnecting; what arrives between the request and the snapshot is
        dropped, and the snapshot counts even if its sequence is lower (restart)."""
        server = FakeNonKYC("resync")
        asked = []

        async def resync_after_live_trade(venue, events):
            if not asked and any(isinstance(e, Trade) and e.qty == D("0.5095") for e in events):
                asked.append(True)
                await venue.resync()

        events = await collect(server, is_last, on_event=resync_after_live_trade)
        assert len(server.received) == 1  # the same connection
        assert [m["method"] for m in server.received[0]] == [
            "subscribeOrderbook",
            "subscribeTrades",
            "subscribeOrderbook",
        ]
        books = book_events(events)
        assert [type(e).__name__ for e in books[-2:]] == ["BookSnapshot", "BookDelta"]
        assert [e.seq for e in books[-2:]] == [5, 6]
        assert all(e.seq <= LAST_SEQ or e.seq in (5, 6) for e in books)  # the previous one, out
        book = L2Book("nk")
        assert all(book.apply(e) for e in books)
        assert book.seq == 6 and book.valid

    async def test_initial_batch_repeats_no_trades(self, rest):
        server = FakeNonKYC("drop", "stay")
        events = await collect(
            server, lambda evs: states(evs).count(("ws", "up")) == 2 and is_last(evs)
        )
        trades = [e for e in events if isinstance(e, Trade)]
        assert len(trades) == len(set(trades))


@pytest.mark.spec("RF-01", "RF-14")
class TestReconnection:
    """Supervisor rules (D-11) with NonKYC's real protocol."""

    async def test_drop_reconnects_by_ws(self, rest):
        server = FakeNonKYC("drop", "stay")
        events = await collect(server, lambda evs: states(evs).count(("ws", "up")) == 2)
        assert states(events) == [("ws", "up"), ("ws", "reconnecting"), ("ws", "up")]
        assert not rest.calls

    async def test_three_failures_polling_and_back_to_ws(self, rest):
        server = FakeNonKYC("error", "error", "error", "stay")
        events = await collect(server, lambda evs: ("ws", "up") in states(evs))
        assert states(events) == [
            ("ws", "reconnecting"),
            ("ws", "reconnecting"),
            ("ws", "reconnecting"),
            ("rest", "up"),
            ("ws", "up"),
        ]
        i = next(i for i, e in enumerate(events) if isinstance(e, ConnState) and e.source == "rest")
        rest_books = [e for e in events[i:] if isinstance(e, BookSnapshot)]
        assert rest_books[0].seq is None and len(rest_books[0].bids) == 20

    async def test_no_snapshot_not_up(self, rest):
        """Connected but dataless: not declared up until the first snapshot."""
        server = FakeNonKYC("silent")
        with pytest.raises(TimeoutError):
            await collect(server, lambda evs: bool(evs), timeout=0.3)


@pytest.mark.spec("RF-01")
async def test_closing_stream_closes_ws(rest):
    """With over 16 unread frames, a clean close would wait websockets' default 10 s:
    `WS_CLOSE_TIMEOUT_S` bounds it (exiting the terminal cannot take 10 s)."""
    server = FakeNonKYC("stay")
    async with serve(server.handler, "127.0.0.1", 0) as srv:
        async with httpx.AsyncClient() as http:
            stream = make_venue(http, srv.sockets[0].getsockname()[1]).market_stream()
            async for ev in stream:
                if isinstance(ev, BookSnapshot):
                    break
            await asyncio.sleep(0.1)  # let the remaining frames arrive and queue up
            await asyncio.wait_for(stream.aclose(), 1.5)


@pytest.mark.spec("RF-01", "RF-02")
class TestWsMessage:
    """`ws_message` decides which WS messages count (T-85)."""

    def test_pair_message_passes_whole(self):
        msg = {"method": "updateTrades", "params": {"symbol": "BTCB2/BTC", "data": []}}
        assert ws_message(msg, "BTCB2/BTC", "frame") is msg

    def test_other_symbol_or_no_params_ignored(self):
        other = {"method": "updateTrades", "params": {"symbol": "NKYC/BTC"}}
        assert ws_message(other, "BTCB2/BTC", "frame") is None
        assert ws_message({"result": True, "id": 1}, "BTCB2/BTC", "frame") is None
        assert ws_message({"params": [1]}, "BTCB2/BTC", "frame") is None

    def test_non_object_is_error(self):
        with pytest.raises(VenueError, match=r"^NonKYC WS: mensaje inesperado '\[1, 2\]'$"):
            ws_message([1, 2], "BTCB2/BTC", "[1, 2]")

    def test_venue_error_is_error(self):
        msg = {"error": {"code": 2001, "message": "Symbol not found"}, "id": 1}
        with pytest.raises(VenueError, match=r"^NonKYC WS: 2001 Symbol not found$"):
            ws_message(msg, "BTCB2/BTC", "frame")
