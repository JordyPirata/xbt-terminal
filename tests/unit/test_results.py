"""Resultado realizado de las órdenes dobles a partir del journal (T-40, RF-25,
CA-11, design.md §6.10, D-44, D-45).

Journal sintético: `order_sent` con el `double` de cada pierna, `order_ack`
con su `order_id`, `fill` (con `client_id`, con solo `order_id` o sin ninguno,
como los de Neoxa), `bot_fire` y `hanging_leg`."""

import json
from datetime import UTC, datetime

import pytest

from btcb2_terminal.core.models import D
from btcb2_terminal.core.results import compute, range_start, read_rows

T0 = int(datetime(2026, 10, 22, 12, 0, tzinfo=UTC).timestamp() * 1000)
DAY = 86_400_000


def ts(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def row(event, at, venue=None, client_id=None, mode="live", **data):
    return {
        "ts": ts(at), "mode": mode, "venue": venue, "event": event,
        "client_id": client_id, "data": data,
    }  # fmt: skip


def sent(cid, venue, side, qty, price, double, at=T0, mode="live"):
    return row("order_sent", at, venue, cid, mode, side=side, qty=qty, price=price, double=double)


def ack(cid, venue, oid, at=T0):
    return row("order_ack", at, venue, cid, order_id=oid, status="open")


def fill(venue, side, qty, price, fee="0", asset="BTC", cid=None, oid=None, at=T0 + 1000, tid=None):
    return row(
        "fill", at, venue, cid, trade_id=tid or f"tr-{venue}-{at}-{qty}", order_id=oid,
        side=side, qty=qty, price=price, fee=fee, fee_asset=asset,
    )  # fmt: skip


def fire(double, net_pct, at=T0):
    return row("bot_fire", at, double=double, net_pct=net_pct, net="0", qty="0")


def plain_double(double="d-1", at=T0, qty="0.01", buy="0.0050", sell="0.0052"):
    """Compra en nx y venta en nk, las dos llenas, fee 0."""
    b, s = f"t-{double}-b", f"t-{double}-s"
    return [
        sent(b, "nx", "buy", qty, buy, double, at),
        sent(s, "nk", "sell", qty, sell, double, at),
        fill("nx", "buy", qty, buy, at=at + 1000),
        fill("nk", "sell", qty, sell, cid=s, at=at + 1000),
    ]


@pytest.mark.spec("RF-25", "CA-11")
class TestResultado:
    def test_doble_llena_sin_fees(self):
        rep = compute(plain_double())
        (d,) = rep.doubles
        assert d.status == "closed"
        assert (d.buy_venue, d.sell_venue) == ("nx", "nk")
        assert d.matched == D("0.01")
        assert d.result == D("0.000002")  # 0.01 × (0.0052 - 0.0050)
        assert d.mismatch == 0

    def test_fees_en_btc_restan(self):
        rows = [
            sent("t-b", "nk", "buy", "0.01", "0.0050", "d-1"),
            sent("t-s", "nx", "sell", "0.01", "0.0052", "d-1"),
            fill("nk", "buy", "0.01", "0.0050", fee="0.0000001", cid="t-b"),
            fill("nx", "sell", "0.01", "0.0052", fee="0.00000011"),
        ]
        (d,) = compute(rows).doubles
        assert d.fees == D("0.00000021")
        assert d.result == D("0.000002") - D("0.00000021")

    def test_fee_en_btcb2_se_valora_al_precio_del_fill(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.00002", asset="BTCB2")
        (d,) = compute(rows).doubles
        assert d.fees == D("0.00002") * D("0.0050")
        assert d.result == D("0.000002") - D("0.0000001")

    def test_fee_sin_asset_y_cero_no_cuenta(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0", asset=None)
        assert compute(rows).doubles[0].fees == 0

    def test_fee_en_otro_token_no_se_valora_y_se_apunta(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="3", asset="NEOX")
        (d,) = compute(rows).doubles
        assert d.fees == 0
        assert d.other_fees == (("NEOX", D("3")),)

    @pytest.mark.spec("RF-24")
    def test_el_resumen_suma_las_fees_en_otro_token(self):
        rows = plain_double("d-1") + plain_double("d-2", T0 + 10_000)
        rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.0025", asset="NKYC")
        rows[6] = fill("nx", "buy", "0.01", "0.0050", fee="0.0020", asset="NKYC", at=T0 + 11_000)
        assert compute(rows).summary.other_fees == (("NKYC", D("0.0045")),)

    def test_varios_fills_por_pierna_a_precio_medio(self):
        rows = [
            sent("t-b", "nx", "buy", "0.03", "0.0051", "d-1"),
            sent("t-s", "nk", "sell", "0.03", "0.0052", "d-1"),
            fill("nx", "buy", "0.01", "0.0049", at=T0 + 500),
            fill("nx", "buy", "0.02", "0.0050", at=T0 + 900),
            fill("nk", "sell", "0.03", "0.0052", cid="t-s"),
        ]
        (d,) = compute(rows).doubles
        assert d.buy_avg == (D("0.01") * D("0.0049") + D("0.02") * D("0.0050")) / D("0.03")
        assert d.result == D("0.03") * D("0.0052") - D("0.000049") - D("0.0001")

    def test_parciales_distintas_casan_el_minimo_y_el_resto_es_descuadre(self):
        rows = [
            sent("t-b", "nx", "buy", "0.05", "0.0050", "d-1"),
            sent("t-s", "nk", "sell", "0.05", "0.0052", "d-1"),
            fill("nx", "buy", "0.05", "0.0050"),
            fill("nk", "sell", "0.03", "0.0052", cid="t-s"),
        ]
        (d,) = compute(rows).doubles
        assert d.status == "closed"
        assert d.matched == D("0.03")
        assert d.mismatch == D("0.02")  # compra de más
        assert d.result == D("0.03") * D("0.0002")

    def test_porcentaje_real_sobre_lo_pagado(self):
        (d,) = compute(plain_double()).doubles
        assert d.result_pct == D("0.000002") / (D("0.01") * D("0.0050")) * 100


@pytest.mark.spec("RF-25", "CA-11")
class TestEstados:
    def test_colgada_con_descuadre_no_suma(self):
        rows = [
            sent("t-b", "nx", "buy", "0.05", "0.0050", "d-1"),
            sent("t-s", "nk", "sell", "0.05", "0.0052", "d-1"),
            fill("nx", "buy", "0.05", "0.0050"),
            fill("nk", "sell", "0.01", "0.0052", cid="t-s"),
            row("hanging_leg", T0 + 31_000, double="d-1", reason="…"),
        ]
        rep = compute(rows)
        assert rep.doubles[0].status == "hanging"
        assert rep.doubles[0].result is None
        assert rep.summary.hanging == 1
        assert rep.summary.closed == 0
        assert rep.summary.total == 0

    def test_colgada_que_luego_se_iguala_cuenta_como_cerrada(self):
        rows = plain_double()
        rows.append(row("hanging_leg", T0 + 31_000, double="d-1", reason="…"))
        assert compute(rows).doubles[0].status == "closed"

    def test_pierna_sin_fills(self):
        rows = plain_double()[:3]  # la venta no se llenó
        rep = compute(rows)
        assert rep.doubles[0].status == "no_fills"
        assert rep.doubles[0].result is None
        assert rep.summary.no_fills == 1

    def test_ninguna_pierna_llena(self):
        rows = plain_double()[:2]
        assert compute(rows).doubles[0].status == "no_fills"


@pytest.mark.spec("RF-25", "CA-11")
class TestOrigen:
    def test_bot_con_neta_esperada(self):
        rows = [fire("d-1", "3.5"), *plain_double()]
        (d,) = compute(rows).doubles
        assert d.origin == "bot"
        assert d.expected_pct == D("3.5")

    def test_manual_sin_neta_esperada(self):
        (d,) = compute(plain_double()).doubles
        assert d.origin == "manual"
        assert d.expected_pct is None

    def test_ordenes_simples_no_son_dobles(self):
        rows = [
            row("order_sent", T0, "nk", "t-x", side="buy", qty="0.01", price="0.005"),
            fill("nk", "buy", "0.01", "0.005", cid="t-x"),
        ]
        assert compute(rows).doubles == ()

    def test_dry_run_se_ignora(self):
        rows = [sent("t-b", "nx", "buy", "0.01", "0.005", "d-1", mode="dry-run")]
        assert compute(rows).doubles == ()


@pytest.mark.spec("RF-25", "CA-11")
class TestAsignacion:
    """Los fills de Neoxa no traen `order_id` ni `client_id` (D-45)."""

    def test_fill_por_order_id_via_order_ack(self):
        rows = plain_double()
        rows[3] = fill("nk", "sell", "0.01", "0.0052", oid="o-9")
        rows.insert(2, ack("t-d-1-s", "nk", "o-9"))
        assert compute(rows).doubles[0].status == "closed"

    def test_sin_ids_va_a_la_orden_compatible(self):
        rows = [*plain_double("d-1", T0), *plain_double("d-2", T0 + 60_000, qty="0.02")]
        rep = compute(rows)
        assert [d.matched for d in rep.doubles] == [D("0.01"), D("0.02")]
        assert rep.summary.unassigned == 0

    def test_precio_fuera_del_limite_no_se_asigna(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0051")  # compra por encima del límite
        rep = compute(rows)
        assert rep.doubles[0].status == "no_fills"
        assert rep.summary.unassigned == 1

    def test_fill_antes_del_envio_no_se_asigna(self):
        rows = plain_double()
        rows[2] = fill("nx", "buy", "0.01", "0.0050", at=T0 - 5000)
        assert compute(rows).summary.unassigned == 1

    def test_no_pasa_de_la_cantidad_de_la_orden(self):
        rows = plain_double()
        rows.insert(3, fill("nx", "buy", "0.01", "0.0050", at=T0 + 1500))  # una de más
        rep = compute(rows)
        assert rep.doubles[0].buy_filled == D("0.01")
        assert rep.summary.unassigned == 1

    def test_dos_candidatas_va_a_la_enviada_justo_antes(self):
        rows = [
            # una compra manual en Neoxa que lleva horas abierta al mismo límite
            row("order_sent", T0 - 3 * 3600_000, "nx", "t-m", side="buy", qty="1", price="0.0050"),
            *plain_double(),
        ]
        rep = compute(rows)
        assert rep.doubles[0].status == "closed"
        assert rep.summary.unassigned == 0

    def test_dos_candidatas_recientes_queda_sin_asignar(self):
        rows = [
            row("order_sent", T0, "nx", "t-m", side="buy", qty="1", price="0.0050"),
            *plain_double(),
        ]
        rep = compute(rows)
        assert rep.doubles[0].status == "no_fills"
        assert rep.summary.unassigned == 1

    def test_orden_rechazada_no_es_candidata(self):
        rows = [
            row("order_sent", T0, "nx", "t-m", side="buy", qty="1", price="0.0050"),
            row("order_error", T0, "nx", "t-m", error="saldo"),
            *plain_double(),
        ]
        assert compute(rows).doubles[0].status == "closed"

    def test_fill_repetido_cuenta_una_vez(self):
        rows = plain_double()
        rows.append(dict(rows[3]))
        assert compute(rows).doubles[0].sell_filled == D("0.01")


@pytest.mark.spec("RF-25", "CA-11")
class TestResumenYRango:
    def test_resumen_y_curva(self):
        rows = [
            *plain_double("d-1", T0),  # +0.000002
            *plain_double("d-2", T0 + 60_000, buy="0.0053"),  # -0.000001
            *plain_double("d-3", T0 + 120_000, sell="0.0055"),  # +0.000005
        ]
        s = compute(rows).summary
        assert s.closed == 3
        assert s.total == D("0.000006")
        assert s.mean == D("0.000002")
        assert (s.best, s.worst) == (D("0.000005"), D("-0.000001"))
        curve = compute(rows).curve
        assert [v for _, v in curve] == [D("0.000002"), D("0.000001"), D("0.000006")]

    def test_sin_dobles(self):
        s = compute([]).summary
        assert (s.closed, s.total, s.mean, s.best, s.worst) == (0, 0, None, None, None)

    def test_desde_filtra_por_inicio_de_la_doble(self):
        rows = [*plain_double("d-1", T0 - DAY), *plain_double("d-2", T0)]
        rep = compute(rows, since_ms=T0 - 1000)
        assert [d.id for d in rep.doubles] == ["d-2"]

    def test_doble_que_cruza_medianoche_es_del_dia_en_que_empezo(self):
        midnight = T0 - 12 * 3600_000
        rows = plain_double("d-1", midnight - 500)  # fills tras medianoche
        assert compute(rows, since_ms=midnight).doubles == ()
        assert compute(rows, since_ms=midnight - DAY).doubles[0].status == "closed"

    def test_rangos(self):
        start = T0 - 12 * 3600_000  # medianoche UTC del día de T0
        assert range_start("dia", T0) == start
        assert range_start("7d", T0) == start - 6 * DAY
        assert range_start("todo", T0) is None


@pytest.mark.spec("RF-25")
class TestLectura:
    def test_lineas_rotas_se_cuentan_y_no_rompen(self):
        good = [json.dumps(r) for r in plain_double()]
        rows, bad = read_rows([good[0], "{roto", "", good[1], '{"sin": "campos"}', *good[2:]])
        assert bad == 2
        assert compute(rows).doubles[0].status == "closed"

    def test_valores_raros_en_una_fila_no_rompen(self):
        rows = plain_double()
        rows.insert(2, row("fill", T0 + 1000, "nx", qty="x", price="0.005", side="buy"))
        rep = compute(rows)
        assert rep.doubles[0].status == "closed"
        assert rep.summary.bad_rows == 1


def fill_fee(venue, tid, fee, asset, oid=None, cid=None, at=T0 + 60_000):
    return row("fill_fee", at, venue, cid, trade_id=tid, order_id=oid, fee=fee, fee_asset=asset)


@pytest.mark.spec("RF-11", "RF-25")
class TestFeeCorregida:
    """d-2e18a2 (D-64): NonKYC rehizo en NKYC la fee de los dos fills de la compra."""

    def rows(self):
        return [
            sent("t-b", "nk", "buy", "0.0036", "0.00433551", "d-1"),
            sent("t-s", "nx", "sell", "0.0036", "0.00438221", "d-1"),
            ack("t-b", "nk", "6add927d"),
            fill("nk", "buy", "0.0018", "0.00433523", fee="0.000000015606828", oid="6add927d",
                 tid="c7"),
            fill("nk", "buy", "0.0018", "0.00432685", fee="0.00000001557666", oid="6add927d",
                 tid="c6"),
            fill("nx", "sell", "0.0036", "0.00441389", fee="0", asset=None, tid="amm"),
        ]  # fmt: skip

    def test_sin_correccion_la_fee_en_btc(self):
        (d,) = compute(self.rows()).doubles
        assert d.fees == D("0.000000031183488") and d.other_fees == ()

    def test_la_correccion_sustituye_la_fee(self):
        rows = [
            *self.rows(),
            fill_fee("nk", "c7", "0.0000893166", "NKYC", oid="6add927d"),
            fill_fee("nk", "c6", "0.0000891439", "NKYC", oid="6add927d"),
        ]
        (d,) = compute(rows).doubles
        assert d.fees == 0
        assert d.other_fees == (("NKYC", D("0.0001784605")),)
        sold = D("0.0036") * D("0.00441389")
        paid = D("0.0018") * D("0.00433523") + D("0.0018") * D("0.00432685")
        assert d.result == sold - paid

    def test_dos_correcciones_cuenta_la_ultima(self):
        rows = [
            *self.rows(),
            fill_fee("nk", "c7", "0.00000002", "BTC"),
            fill_fee("nk", "c7", "0.0000893166", "NKYC"),
        ]
        (d,) = compute(rows).doubles
        assert d.fees == D("0.00000001557666")
        assert d.other_fees == (("NKYC", D("0.0000893166")),)

    def test_de_un_trade_desconocido_se_ignora(self):
        rows = [*self.rows(), fill_fee("nk", "zz", "1", "NKYC")]
        (d,) = compute(rows).doubles
        assert d.other_fees == () and compute(rows).summary.bad_rows == 0


@pytest.mark.spec("RF-24", "RF-25")
class TestFeeEnTokenValorada:
    """D-65: la fee en NKYC de d-2e18a2, valorada al mid de NKYC/BTC, resta."""

    PRICE = D("0.00013089")
    NKYC = D("0.0001784605")
    SOLD = D("0.0036") * D("0.00441389")
    PAID = D("0.0018") * D("0.00433523") + D("0.0018") * D("0.00432685")

    def rows(self):
        return [
            *TestFeeCorregida().rows(),
            fill_fee("nk", "c7", "0.0000893166", "NKYC", oid="6add927d"),
            fill_fee("nk", "c6", "0.0000891439", "NKYC", oid="6add927d"),
        ]

    def test_con_precio_se_resta(self):
        rep = compute(self.rows(), prices={"NKYC": self.PRICE})
        (d,) = rep.doubles
        assert d.token_fees == self.NKYC * self.PRICE
        assert d.result == self.SOLD - self.PAID - self.NKYC * self.PRICE
        assert d.other_fees == (("NKYC", self.NKYC),)  # la cantidad se sigue mostrando
        assert rep.summary.total == d.result
        assert rep.summary.other_fees == ()  # nada sin valorar
        assert rep.summary.prices == (("NKYC", self.PRICE),)

    def test_pagar_en_nkyc_sale_mejor_que_en_btc(self):
        """La fee de 0.2 % en BTC (antes de la corrección) frente a 0.15 % en NKYC."""
        (in_btc,) = compute(TestFeeCorregida().rows()).doubles
        (in_nkyc,) = compute(self.rows(), prices={"NKYC": self.PRICE}).doubles
        assert in_nkyc.result > in_btc.result

    def test_sin_precio_como_antes(self):
        rep = compute(self.rows())
        (d,) = rep.doubles
        assert d.token_fees == 0 and d.result == self.SOLD - self.PAID
        assert rep.summary.other_fees == (("NKYC", self.NKYC),)
        assert rep.summary.prices == ()

    def test_precio_de_otro_activo_no_cuenta(self):
        rep = compute(self.rows(), prices={"NEOX": D("1")})
        assert rep.doubles[0].token_fees == 0
        assert rep.summary.other_fees == (("NKYC", self.NKYC),)
        assert rep.summary.prices == ()
