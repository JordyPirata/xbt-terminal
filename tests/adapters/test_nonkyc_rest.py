"""REST público de NonKYC contra respuestas reales grabadas el 2026-10-20
(tests/fixtures/nonkyc/). Precios y cantidades vienen como strings; `minQuote` y
las velas, como números JSON: todo debe llegar como Decimal exacto."""

from pathlib import Path

import httpx
import pytest
import respx

from btcb2_terminal.adapters.base import MarketData, VenueError
from btcb2_terminal.adapters.nonkyc import MAX_TRADES, REST_URL, NonKYCVenue
from btcb2_terminal.core.models import D, Level

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nonkyc"
NOW = 1_792_509_000_000


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


def make(limiter, http, symbol="BTCB2/BTC"):
    return NonKYCVenue(
        symbol, limiter, maker_fee=D("0.002"), taker_fee=D("0.002"), http=http, clock=lambda: NOW
    )


@pytest.fixture
async def venue(limiter):
    async with httpx.AsyncClient() as http:
        yield make(limiter, http)


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{REST_URL}/market/getbysymbol/BTCB2_BTC").respond(text=fixture("market.json"))
        mock.get(f"{REST_URL}/market/orderbook").respond(text=fixture("orderbook.json"))
        mock.get(f"{REST_URL}/market/trades").respond(text=fixture("trades.json"))
        mock.get(f"{REST_URL}/market/candles", params={"resolution": "5"}).respond(
            text=fixture("candles_5m.json")
        )
        for resolution, tf in (("240", "4h"), ("1440", "1d")):
            mock.get(f"{REST_URL}/market/candles", params={"resolution": resolution}).respond(
                text=fixture(f"candles_{tf}.json")
            )
        mock.get(f"{REST_URL}/market/candles", params={"resolution": "60"}).respond(
            text=fixture("candles_1h.json")
        )
        yield mock


@pytest.mark.spec("RNF-09")
def test_cumple_la_interfaz_comun(limiter):
    assert isinstance(make(limiter, None), MarketData)


@pytest.mark.spec("RNF-09", "RNF-10")
class TestMarket:
    async def test_reglas_del_par(self, venue, api):
        m = await venue.market()
        assert (m.venue, m.symbol, m.base, m.quote) == ("nk", "BTCB2/BTC", "BTCB2", "BTC")
        assert m.tick_size == D("0.00000001") and m.lot_size == D("0.0001")
        assert m.min_qty == D("0.0001")  # minimumQuantity 0 → el lote
        assert m.min_notional == D("0.000012609180967214268")
        assert (m.maker_fee, m.taker_fee) == (D("0.002"), D("0.002"))  # de la config (PA-04)

    async def test_min_quote_inactivo(self, venue, api):
        api.get(f"{REST_URL}/market/getbysymbol/BTCB2_BTC").respond(
            text=fixture("market.json").replace(
                '"isMinQuoteActive":true', '"isMinQuoteActive":false'
            )
        )
        assert (await venue.market()).min_notional == D("0")

    async def test_mercado_que_no_existe(self, limiter, api):
        api.get(f"{REST_URL}/market/getbysymbol/BTCB2_XMR").respond(
            400,
            json={"error": {"code": 2001, "message": "Market not found", "description": "x"}},
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(VenueError, match="Market not found"):
                await make(limiter, http, "BTCB2/XMR").market()

    async def test_venue_devuelve_otro_simbolo(self, venue, api):
        """Defensa en profundidad (RNF-10): nunca operar un par distinto del pedido."""
        api.get(f"{REST_URL}/market/getbysymbol/BTCB2_BTC").respond(
            text=fixture("market.json").replace('"symbol":"BTCB2/BTC"', '"symbol":"BTCB2/USDT"')
        )
        with pytest.raises(VenueError, match="USDT"):
            await venue.market()

    async def test_mercado_pausado_o_inactivo(self, venue, api):
        api.get(f"{REST_URL}/market/getbysymbol/BTCB2_BTC").respond(
            text=fixture("market.json").replace('"isActive":true', '"isActive":false')
        )
        with pytest.raises(VenueError, match="inactivo"):
            await venue.market()


@pytest.mark.spec("RF-01", "RF-02")
class TestBook:
    async def test_snapshot_en_decimal(self, venue, api):
        snap = await venue.book_snapshot(20)
        params = api.calls.last.request.url.params
        assert (params["symbol"], params["limit"]) == ("BTCB2/BTC", "20")
        assert (snap.venue, snap.ts) == ("nk", 1792508949688)  # hora del venue
        assert snap.seq is None  # el sequence REST no es el del WS (D-10)
        assert len(snap.bids) == 20 and len(snap.asks) == 20
        assert snap.bids[0] == Level(D("0.00721141"), D("0.0014"))
        assert snap.asks[0] == Level(D("0.00724756"), D("0.0014"))
        assert snap.bids[-1] == Level(D("0.00644898"), D("0.0162"))
        assert not any(lv.amm for lv in snap.bids + snap.asks)

    async def test_orden_de_niveles(self, venue, api):
        snap = await venue.book_snapshot(20)
        bids, asks = [lv.price for lv in snap.bids], [lv.price for lv in snap.asks]
        assert bids == sorted(bids, reverse=True)
        assert asks == sorted(asks)


@pytest.mark.spec("RF-01")
class TestTrades:
    async def test_cronologicos_y_en_decimal(self, venue, api):
        trades = await venue.recent_trades(20)
        params = api.calls.last.request.url.params
        assert (params["symbol"], params["limit"]) == ("BTCB2/BTC", "20")
        assert len(trades) == 20
        assert [t.ts for t in trades] == sorted(t.ts for t in trades)
        newest, oldest = trades[-1], trades[0]
        assert (newest.side, newest.price, newest.qty) == ("sell", D("0.00724092"), D("0.3109"))
        assert newest.ts == 1792508948406
        assert (oldest.side, oldest.qty, oldest.ts) == ("buy", D("0.0139"), 1792508026567)

    async def test_limite_maximo_de_la_api(self, venue, api):
        await venue.recent_trades(5000)
        assert api.calls.last.request.url.params["limit"] == str(MAX_TRADES)


@pytest.mark.spec("RF-03")
class TestCandles:
    @pytest.mark.parametrize(
        "tf,resolution,step",
        [
            ("5m", "5", 300_000),
            ("1h", "60", 3_600_000),
            ("4h", "240", 14_400_000),
            ("1d", "1440", 86_400_000),
        ],
    )
    async def test_velas_del_venue(self, venue, api, tf, resolution, step):
        candles = await venue.candles(tf, 5)
        params = api.calls.last.request.url.params
        assert params["symbol"] == "BTCB2/BTC" and params["resolution"] == resolution
        assert params["countBack"] == "5" and params["firstDataRequest"] == "1"
        assert len(candles) == 5 and all(c.ts_open % step == 0 for c in candles)
        assert [c.ts_open for c in candles] == sorted(c.ts_open for c in candles)

    async def test_valores_exactos(self, venue, api):
        first = (await venue.candles("5m", 5))[0]
        assert first.ts_open == 1792507500000
        assert (first.open, first.high, first.low, first.close, first.volume) == (
            D("0.00724384"),
            D("0.00724448"),
            D("0.00721142"),
            D("0.00722185"),
            D("3.212"),
        )

    async def test_1m_se_construye_con_trades(self, venue, api):
        """NonKYC no tiene velas de 1m (D-10): salen de los últimos trades."""
        candles = await venue.candles("1m", 500)
        assert api.calls.last.request.url.path.endswith("/market/trades")
        assert api.calls.last.request.url.params["limit"] == str(MAX_TRADES)
        trades = await venue.recent_trades(20)
        assert all(c.ts_open % 60_000 == 0 for c in candles)
        assert candles[0].ts_open == trades[0].ts - trades[0].ts % 60_000
        assert candles[-1].ts_open == trades[-1].ts - trades[-1].ts % 60_000
        assert len(candles) == (candles[-1].ts_open - candles[0].ts_open) // 60_000 + 1
        assert sum(c.volume for c in candles) == sum(t.qty for t in trades)
        assert candles[0].open == trades[0].price and candles[-1].close == trades[-1].price

    async def test_1m_respeta_el_limite(self, venue, api):
        candles = await venue.candles("1m", 3)
        assert len(candles) == 3

    async def test_1m_con_el_tope_de_trades_descarta_la_primera(self, venue, api):
        """Con 500 trades la vela más vieja puede estar cortada a medias."""
        trades = [
            {
                "id": f"t{i}",
                "price": "0.0072",
                "type": "buy",
                "baseVolume": "1",
                "quoteVolume": "0.0072",
                "timestamp": NOW - i * 30_000,  # dos por minuto
            }
            for i in range(MAX_TRADES)
        ]
        api.get(f"{REST_URL}/market/trades").respond(json=trades)
        candles = await venue.candles("1m", 1000)
        oldest_bucket = (NOW - (MAX_TRADES - 1) * 30_000) // 60_000 * 60_000
        assert candles[0].ts_open == oldest_bucket + 60_000
        assert all(c.volume == D("2") for c in candles[:-1])
        assert candles[-1].volume == D("1")  # la vela en curso solo tiene el trade de NOW


@pytest.mark.spec("RF-01")
class TestErrores:
    async def test_error_del_venue(self, venue, api):
        api.get(f"{REST_URL}/market/orderbook").respond(
            400, json={"error": {"code": 2001, "message": "Market not found"}}
        )
        with pytest.raises(VenueError, match=r"2001.*Market not found"):
            await venue.book_snapshot(20)

    async def test_429(self, venue, api):
        api.get(f"{REST_URL}/market/trades").respond(429, text="Too Many Requests")
        with pytest.raises(VenueError, match="429"):
            await venue.recent_trades(20)

    async def test_no_es_json(self, venue, api):
        api.get(f"{REST_URL}/market/orderbook").respond(502, text="<html>Bad gateway</html>")
        with pytest.raises(VenueError, match="502"):
            await venue.book_snapshot(20)

    async def test_campo_que_falta(self, venue, api):
        api.get(f"{REST_URL}/market/trades").respond(json=[{"id": "x", "type": "buy"}])
        with pytest.raises(VenueError):
            await venue.recent_trades(20)


@pytest.mark.spec("RNF-05")
async def test_toda_peticion_pasa_por_el_limitador(venue, api, limiter):
    await venue.market()
    await venue.book_snapshot(20)
    await venue.recent_trades(20)
    await venue.candles("5m", 5)
    await venue.candles("1m", 5)
    assert limiter.kinds == ["market"] * 5
    assert len(api.calls) == 5


@pytest.mark.spec("RF-14")
async def test_las_peticiones_miden_latencia(venue, api):
    assert venue.state("rest", "up").latency_ms is None
    await venue.book_snapshot(20)
    assert venue.state("rest", "up").latency_ms > 0
