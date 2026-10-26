import pytest

from btcb2_terminal.core.candles import TIMEFRAME_MS, CandleBuilder
from btcb2_terminal.core.models import Candle, D, Trade

MIN = 60_000
T0 = 1_759_996_800_000  # múltiplo exacto de 1 h


def trade(ts: int, price: str, qty: str = "0.1") -> Trade:
    return Trade("nk", D(price), D(qty), "buy", ts)


def candle(ts_open: int, o: str, h: str, low: str, c: str, v: str) -> Candle:
    return Candle(ts_open, D(o), D(h), D(low), D(c), D(v))


def test_t0_alineado_a_la_hora():
    assert T0 % TIMEFRAME_MS["1h"] == 0


@pytest.mark.spec("RF-03")
class TestTradesEnVivo:
    def test_trades_del_mismo_minuto(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0 + 1_000, "1000", "0.1"))
        b.on_trade(trade(T0 + 20_000, "1010", "0.2"))
        b.on_trade(trade(T0 + 40_000, "995", "0.3"))
        b.on_trade(trade(T0 + 59_999, "1002", "0.4"))
        assert b.candles == (candle(T0, "1000", "1010", "995", "1002", "1.0"),)

    def test_cambio_de_vela(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.on_trade(trade(T0 + MIN, "1005"))
        assert [c.ts_open for c in b.candles] == [T0, T0 + MIN]
        assert b.candles[-1].open == D("1005")

    def test_minuto_sin_trades_se_rellena_plano(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.on_trade(trade(T0 + 3 * MIN + 5, "1020"))
        c = b.candles
        assert [x.ts_open for x in c] == [T0 + i * MIN for i in range(4)]
        assert c[1] == candle(T0 + MIN, "1000", "1000", "1000", "1000", "0")
        assert c[3].open == D("1020")

    def test_trade_atrasado_se_ignora(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0 + MIN, "1000"))
        b.on_trade(trade(T0 + 10, "500"))
        assert b.candles == (candle(T0 + MIN, "1000", "1000", "1000", "1000", "0.1"),)

    @pytest.mark.parametrize(
        "tf,step", [("5m", 5 * MIN), ("1h", 60 * MIN), ("4h", 240 * MIN), ("1d", 1440 * MIN)]
    )
    def test_otros_timeframes(self, tf, step):
        start = T0 - T0 % step  # 4h y 1d empiezan en múltiplos exactos (00:00 UTC)
        b = CandleBuilder(tf)
        b.on_trade(trade(start + step - 1, "1000"))
        b.on_trade(trade(start + step, "1001"))
        assert [c.ts_open for c in b.candles] == [start, start + step]

    def test_el_dia_empieza_a_las_0_utc(self):
        b = CandleBuilder("1d")
        b.on_trade(trade(1_792_540_800_000 + 5 * 3_600_000, "1000"))  # 2026-10-20 05:00 UTC
        assert b.candles[0].ts_open == 1_792_540_800_000  # 2026-10-20 00:00 UTC


@pytest.mark.spec("RF-03")
class TestHistorico:
    def test_trade_actualiza_la_ultima_vela_del_historico(self):
        b = CandleBuilder("1m")
        b.load(
            [
                candle(T0 - MIN, "990", "995", "985", "992", "3"),
                candle(T0, "992", "999", "991", "998", "1"),
            ]
        )
        b.on_trade(trade(T0 + 30_000, "1003", "0.5"))
        assert b.candles[-1] == candle(T0, "992", "1003", "991", "1003", "1.5")
        assert len(b.candles) == 2

    def test_load_ordena_y_reemplaza(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1"))
        b.load([candle(T0, "2", "2", "2", "2", "0"), candle(T0 - MIN, "1", "1", "1", "1", "0")])
        assert [c.ts_open for c in b.candles] == [T0 - MIN, T0]

    def test_load_rellena_huecos_del_historico(self):
        b = CandleBuilder("1m")
        b.load([candle(T0, "1", "1", "1", "1", "1"), candle(T0 + 2 * MIN, "3", "3", "3", "3", "1")])
        assert [c.ts_open for c in b.candles] == [T0, T0 + MIN, T0 + 2 * MIN]

    def test_load_rechaza_velas_desalineadas(self):
        with pytest.raises(ValueError):
            CandleBuilder("5m").load([candle(T0 + MIN, "1", "1", "1", "1", "0")])

    def test_limite_de_velas_guardadas(self):
        b = CandleBuilder("1m", max_candles=3)
        for i in range(5):
            b.on_trade(trade(T0 + i * MIN, str(1000 + i)))
        assert [c.ts_open for c in b.candles] == [T0 + 2 * MIN, T0 + 3 * MIN, T0 + 4 * MIN]

    def test_hueco_enorme_no_genera_velas_de_mas(self):
        b = CandleBuilder("1m", max_candles=3)
        b.on_trade(trade(T0, "1000"))
        b.on_trade(trade(T0 + 10_000_000 * MIN, "1001"))
        c = b.candles
        assert [x.ts_open for x in c] == [T0 + (10_000_000 - k) * MIN for k in (2, 1, 0)]
        assert c[0].close == D("1000") and c[-1].open == D("1001")


@pytest.mark.spec("RF-03")
class TestRoll:
    def test_avanza_aunque_no_haya_trades(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.roll(T0 + 2 * MIN + 1)
        assert [c.ts_open for c in b.candles] == [T0, T0 + MIN, T0 + 2 * MIN]
        assert b.candles[-1].close == D("1000") and b.candles[-1].volume == D("0")

    def test_dentro_de_la_misma_vela_no_hace_nada(self):
        b = CandleBuilder("1m")
        b.on_trade(trade(T0, "1000"))
        b.roll(T0 + 59_999)
        assert len(b.candles) == 1

    def test_sin_velas_no_hace_nada(self):
        b = CandleBuilder("1m")
        b.roll(T0)
        assert b.candles == ()
