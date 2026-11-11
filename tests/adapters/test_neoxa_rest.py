"""Neoxa public REST against real responses recorded on 2026-10-20
(tests/fixtures/neoxa/). Neoxa returns JSON floats: everything must arrive as the
exact Decimal of the text, never through float."""

from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from xbt_terminal.adapters.base import MarketData, VenueError
from xbt_terminal.adapters.neoxa import REST_URL, NeoxaVenue
from xbt_terminal.core.models import D, Level

FIXTURES = Path(__file__).parent.parent / "fixtures" / "neoxa"
API = REST_URL + "/exchange"
NOW = 1_792_506_300_000


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


@pytest.fixture
async def venue(limiter):
    async with httpx.AsyncClient(base_url=REST_URL) as http:
        yield NeoxaVenue("BTCB2_BTC", limiter, http=http, clock=lambda: NOW)


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{API}/pairs").respond(text=fixture("pairs.json"))
        mock.get(f"{API}/orderbook/BTCB2_BTC").respond(text=fixture("orderbook.json"))
        mock.get(f"{API}/trades/BTCB2_BTC").respond(text=fixture("trades.json"))
        for tf in ("1m", "5m", "1h", "4h", "1d"):
            mock.get(f"{API}/candles/BTCB2_BTC", params={"interval": tf}).respond(
                text=fixture(f"candles_{tf}.json")
            )
        yield mock


@pytest.mark.spec("RNF-09")
def test_fulfils_the_common_interface(limiter):
    assert isinstance(NeoxaVenue("BTCB2_BTC", limiter), MarketData)


@pytest.mark.spec("RNF-09", "RNF-10")
class TestMarket:
    async def test_pair_rules(self, venue, api):
        market = await venue.market()
        assert (market.venue, market.symbol, market.base, market.quote) == (
            "nx",
            "BTCB2_BTC",
            "BTCB2",
            "BTC",
        )
        assert market.tick_size == D("0.00000001") and market.lot_size == D("0.00000001")
        assert market.min_qty == D("1e-8")
        assert market.min_notional == D("0")
        assert (market.maker_fee, market.taker_fee) == (D("0.001"), D("0.002"))

    async def test_nonexistent_pair(self, limiter, api):
        async with httpx.AsyncClient(base_url=REST_URL) as http:
            with pytest.raises(VenueError, match="BTCB2_XMR"):
                await NeoxaVenue("BTCB2_XMR", limiter, http=http).market()

    async def test_venue_with_quote_different_from_symbol(self, limiter, api):
        """Defence in depth (RNF-10): the symbol says BTC, the venue must say BTC."""
        bad = fixture("pairs.json").replace('"quoteToken":"BTC"', '"quoteToken":"USDT"')
        api.get(f"{API}/pairs").respond(text=bad)
        async with httpx.AsyncClient(base_url=REST_URL) as http:
            with pytest.raises(VenueError, match="USDT"):
                await NeoxaVenue("BTCB2_BTC", limiter, http=http).market()


@pytest.mark.spec("RF-01", "RF-02")
class TestBook:
    async def test_snapshot_in_decimal_with_amm(self, venue, api):
        snap = await venue.book_snapshot(20)
        assert api.calls.last.request.url.params["depth"] == "20"
        assert (snap.venue, snap.ts, snap.seq) == ("nx", NOW, None)
        assert len(snap.bids) == 20 and len(snap.asks) == 20
        assert snap.bids[0] == Level(D("0.00713454"), D("0.12668383"), amm=True)
        assert snap.bids[2] == Level(D("0.00705"), D("0.37077319"), amm=False)
        assert snap.asks[0] == Level(D("0.00722875"), D("0.0630941"), amm=False)
        assert snap.asks[-1] == Level(D("0.0075743"), D("0.46485224"), amm=True)
        assert sum(price_level.amm for price_level in snap.bids + snap.asks) == 10

    async def test_level_order(self, venue, api):
        snap = await venue.book_snapshot(20)
        bids, asks = (
            [price_level.price for price_level in snap.bids],
            [price_level.price for price_level in snap.asks],
        )
        assert bids == sorted(bids, reverse=True)
        assert asks == sorted(asks)

    async def test_same_amm_and_order_price_add_up(self, venue, api):
        api.get(f"{API}/orderbook/BTCB2_BTC").respond(
            text='{"success":true,"pair":"BTCB2_BTC","bids":['
            '{"price":0.0071,"quantity":0.5,"isAmm":true},'
            '{"price":0.0071,"quantity":0.25,"orders":1},'
            '{"price":0.007,"quantity":1,"orders":2}],"asks":[]}'
        )
        snap = await venue.book_snapshot(20)
        assert snap.bids == (Level(D("0.0071"), D("0.75"), amm=True), Level(D("0.007"), D("1")))


@pytest.mark.spec("RF-01")
class TestTrades:
    async def test_chronological_and_decimal(self, venue, api):
        trades = await venue.recent_trades(20)
        assert api.calls.last.request.url.params["limit"] == "20"
        assert len(trades) == 20
        assert [trade.ts for trade in trades] == sorted(trade.ts for trade in trades)
        newest = trades[-1]
        assert (newest.venue, newest.side, newest.ts) == ("nx", "sell", 1792506216539)
        assert (newest.price, newest.qty) == (D("0.00719377"), D("11.325293"))
        assert trades[0].ts == 1792505272467 and trades[0].qty == D("0.4851")

    async def test_api_maximum_limit(self, venue, api):
        await venue.recent_trades(500)
        assert api.calls.last.request.url.params["limit"] == "100"


@pytest.mark.spec("RF-03")
class TestCandles:
    @pytest.mark.parametrize(
        "tf,step",
        [
            ("1m", 60_000),
            ("5m", 300_000),
            ("1h", 3_600_000),
            ("4h", 14_400_000),
            ("1d", 86_400_000),
        ],
    )
    async def test_aligned_ms_candles(self, venue, api, tf, step):
        candles = await venue.candles(tf, 5)
        params = api.calls.last.request.url.params
        assert (params["interval"], params["limit"]) == (tf, "5")
        assert len(candles) == 5
        assert all(candle.ts_open % step == 0 for candle in candles)
        assert [candle.ts_open for candle in candles] == sorted(
            candle.ts_open for candle in candles
        )

    async def test_exact_values(self, venue, api):
        first = (await venue.candles("5m", 5))[0]
        assert first.ts_open == 1792504800_000
        assert (first.open, first.high, first.low, first.close) == (
            D("0.00707843"),
            D("0.00719377"),
            D("0.007"),
            D("0.00719377"),
        )
        assert first.volume == D("5.59998756")
        assert isinstance(first.volume, Decimal)


@pytest.mark.spec("RF-01")
class TestErrors:
    async def test_invalid_pair_400(self, venue, api):
        api.get(f"{API}/orderbook/BTCB2_BTC").respond(400, json={"error": "Invalid trading pair"})
        with pytest.raises(VenueError, match="Invalid trading pair"):
            await venue.book_snapshot(20)

    async def test_success_false(self, venue, api):
        api.get(f"{API}/trades/BTCB2_BTC").respond(
            json={"success": False, "error": "mantenimiento"}
        )
        with pytest.raises(VenueError, match="mantenimiento"):
            await venue.recent_trades(20)

    async def test_429(self, venue, api):
        api.get(f"{API}/pairs").respond(429, text="Too Many Requests")
        with pytest.raises(VenueError, match="429"):
            await venue.market()

    async def test_response_not_json(self, venue, api):
        api.get(f"{API}/pairs").respond(502, text="<html>Bad gateway</html>")
        with pytest.raises(VenueError, match="502"):
            await venue.market()

    async def test_missing_field(self, venue, api):
        api.get(f"{API}/trades/BTCB2_BTC").respond(
            json={"success": True, "trades": [{"trade_id": "x", "side": "buy"}]}
        )
        with pytest.raises(VenueError):
            await venue.recent_trades(20)


@pytest.mark.spec("RNF-03")
async def test_every_request_passes_limiter(venue, api, limiter):
    await venue.market()
    await venue.book_snapshot(20)
    await venue.recent_trades(20)
    await venue.candles("1m", 5)
    assert limiter.kinds == ["market"] * 4
    assert len(api.calls) == 4


@pytest.mark.spec("RF-14")
async def test_requests_measure_latency(venue, api):
    assert venue.state("rest", "up").latency_ms is None
    await venue.book_snapshot(20)
    assert venue.state("rest", "up").latency_ms > 0
