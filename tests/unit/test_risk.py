"""Mercado de referencia: mid 1000 (999/1001), tick 0.01, lote 0.0001.
Saldos: 300 USDT + 0.1 BTCB2 → valor custodiado 400 USDT, tope 500.
Inventario total de los dos venues 400; tope por orden 15 % → 60 (D-19).
"""

from dataclasses import replace

import pytest

from xbt_terminal.core.models import Balance, D, MarketSpec, Order, OrderRequest
from xbt_terminal.core.risk import RiskContext, RiskLimits, check

NOW = 1_760_000_000_000

MARKET = MarketSpec(
    venue="nk",
    symbol="BTCB2/USDT",
    base="BTCB2",
    quote="USDT",
    tick_size=D("0.01"),
    lot_size=D("0.0001"),
    min_qty=D("0.001"),
    min_notional=D("1"),
    maker_fee=D("0.002"),
    taker_fee=D("0.002"),
)

LIMITS = RiskLimits(
    max_order_pct=D("15"),
    max_mid_deviation_pct=D("5"),
    max_venue_exposure=D("500"),
    stale_after_s=15,
    hanging_leg_after_s=30,
)


def ctx(**kw) -> RiskContext:
    fields = dict(
        market=MARKET,
        best_bid=D("999"),
        best_ask=D("1001"),
        book_ts=NOW - 1_000,
        now=NOW,
        open_orders=(),
        balances={"USDT": Balance("USDT", D("300")), "BTCB2": Balance("BTCB2", D("0.1"))},
        inventory_value=D("400"),
    )
    fields.update(kw)
    return RiskContext(**fields)


def req(side="buy", qty="0.01", price="1000", venue="nk") -> OrderRequest:
    return OrderRequest(venue=venue, side=side, qty=D(qty), price=D(price), client_id="t-1")


def own(side, price, venue="nk", status="open") -> Order:
    return Order(venue, f"o-{side}-{price}", None, side, D(price), D("0.01"), D("0"), status, NOW)


def codes(request, context=None, limits=LIMITS) -> list[str]:
    return [v.code for v in check(request, context or ctx(), limits)]


@pytest.mark.spec("RF-10", "CA-04")
class TestCadaRegla:
    def test_orden_valida_no_viola_nada(self):
        assert codes(req("buy")) == []
        assert codes(req("sell")) == []

    def test_order_size(self):
        """15 % de 400 = 60: el límite exacto se permite."""
        assert codes(req(qty="0.06", price="1000")) == []
        assert codes(
            req(qty="0.05", price="1200.01"), limits=replace(LIMITS, max_mid_deviation_pct=D("50"))
        ) == ["order_size"]

    def test_order_size_sigue_al_inventario(self):
        assert codes(req(qty="0.06", price="1000"), ctx(inventory_value=D("399.99"))) == [
            "order_size"
        ]
        assert codes(req(qty="0.06", price="1000"), ctx(inventory_value=D("800"))) == []

    def test_sin_inventario_calculable_se_rechaza(self):
        """Fallar cerrado: sin saldos o sin mid de un venue no hay tope que aplicar."""
        assert codes(req(), ctx(inventory_value=None)) == ["order_size"]

    def test_inventario_cero(self):
        assert codes(req(), ctx(inventory_value=D("0"))) == ["order_size"]

    def test_mid_deviation(self):
        assert codes(req(price="950")) == []  # justo el 5 %: se permite
        assert codes(req(price="949.99")) == ["mid_deviation"]
        assert codes(req("sell", price="1050.01")) == ["mid_deviation"]

    def test_tick(self):
        assert codes(req(price="1000.005")) == ["tick"]

    def test_lot(self):
        assert codes(req(qty="0.01005")) == ["lot"]

    def test_min_qty(self):
        m = replace(MARKET, min_notional=D("0.5"))
        assert codes(req(qty="0.0009"), ctx(market=m)) == ["min_qty"]

    def test_min_notional(self):
        m = replace(MARKET, min_notional=D("20"))
        assert codes(req(qty="0.01", price="1000"), ctx(market=m)) == ["min_notional"]

    def test_saldo_insuficiente_compra_incluye_fee(self):
        # 0.05 × 1000 = 50 USDT + 0.2 % fee = 50.1 > 50.05
        poco = {"USDT": Balance("USDT", D("50.05")), "BTCB2": Balance("BTCB2", D("0"))}
        assert codes(req(qty="0.05"), ctx(balances=poco)) == ["insufficient_balance"]

    def test_saldo_insuficiente_venta(self):
        assert codes(
            req("sell", qty="0.05"),
            ctx(balances={"USDT": Balance("USDT", D("300")), "BTCB2": Balance("BTCB2", D("0.04"))}),
        ) == ["insufficient_balance"]

    def test_sin_saldo_del_activo_cuenta_como_cero(self):
        assert codes(req("sell"), ctx(balances={"USDT": Balance("USDT", D("300"))})) == [
            "insufficient_balance"
        ]

    def test_lo_bloqueado_en_ordenes_no_cuenta_como_disponible(self):
        bal = {
            "USDT": Balance("USDT", D("5"), locked=D("295")),
            "BTCB2": Balance("BTCB2", D("0.1")),
        }
        assert codes(req(qty="0.01"), ctx(balances=bal)) == ["insufficient_balance"]


@pytest.mark.spec("RF-10", "CA-04")
class TestCruceDeOrdenPropia:
    def test_compra_que_alcanza_mi_ask(self):
        assert codes(req("buy", price="1000"), ctx(open_orders=(own("sell", "1000"),))) == [
            "self_cross"
        ]

    def test_compra_por_debajo_de_mi_ask_no_cruza(self):
        assert codes(req("buy", price="999.99"), ctx(open_orders=(own("sell", "1000"),))) == []

    def test_venta_que_alcanza_mi_bid(self):
        assert codes(req("sell", price="1000"), ctx(open_orders=(own("buy", "1000.5"),))) == [
            "self_cross"
        ]

    def test_se_compara_con_el_ask_mas_bajo(self):
        mine = (own("sell", "1030"), own("sell", "1010"))
        assert codes(req("buy", price="1015"), ctx(open_orders=mine)) == ["self_cross"]

    def test_ordenes_de_otro_venue_o_terminadas_no_cuentan(self):
        mine = (own("sell", "1000", venue="nx"), own("sell", "990", status="filled"))
        assert codes(req("buy", price="1000"), ctx(open_orders=mine)) == []


@pytest.mark.spec("RF-10", "CA-04")
class TestExposicionPorVenue:
    """PA-06 (a): valor custodiado tras la orden = USDT + BTCB2 × mid."""

    def test_ya_por_encima_del_tope(self):
        rico = {"USDT": Balance("USDT", D("450")), "BTCB2": Balance("BTCB2", D("0.06"))}
        assert codes(req(), ctx(balances=rico)) == ["venue_exposure"]

    def test_cuenta_lo_bloqueado_en_ordenes(self):
        bal = {
            "USDT": Balance("USDT", D("300"), locked=D("200")),
            "BTCB2": Balance("BTCB2", D("0.01")),
        }
        assert codes(req(), ctx(balances=bal)) == ["venue_exposure"]

    def test_se_evalua_despues_de_la_orden(self):
        # antes: 499 USDT; comprar 0.05 a 950 (mid 1000) suma 0.05 × 50 = 2.5 → 501.5
        bal = {"USDT": Balance("USDT", D("399")), "BTCB2": Balance("BTCB2", D("0.1"))}
        assert codes(req(qty="0.05", price="950"), ctx(balances=bal)) == ["venue_exposure"]
        assert codes(req("sell", qty="0.05", price="950"), ctx(balances=bal)) == []


@pytest.mark.spec("RF-10", "CA-04")
class TestDatosViejos:
    def test_sin_mid(self):
        assert "stale_data" in codes(req(), ctx(best_ask=None))

    def test_book_viejo(self):
        assert codes(req(), ctx(book_ts=NOW - 15_001)) == ["stale_data"]
        assert codes(req(), ctx(book_ts=NOW - 15_000)) == []

    def test_sin_book(self):
        assert "stale_data" in codes(req(), ctx(best_bid=None, best_ask=None, book_ts=None))

    def test_book_cruzado(self):
        assert "stale_data" in codes(req(), ctx(best_bid=D("1002"), best_ask=D("1001")))


@pytest.mark.spec("RF-10", "CA-04")
def test_devuelve_todas_las_violaciones():
    r = req("buy", qty="0.10005", price="1100.001")
    poco = {"USDT": Balance("USDT", D("100")), "BTCB2": Balance("BTCB2", D("0.1"))}
    got = codes(r, ctx(open_orders=(own("sell", "1050"),), balances=poco))
    assert set(got) == {
        "order_size",
        "mid_deviation",
        "tick",
        "lot",
        "self_cross",
        "insufficient_balance",
    }


@pytest.mark.spec("RF-10")
def test_mensajes_legibles():
    (v,) = check(req(qty="0.0601", price="1000"), ctx(), LIMITS)
    assert v.message == (
        "nocional 60.10000000 > 15 % del inventario total (400.00000000 USDT) = 60.00000000"
    )
    (v,) = check(req(), ctx(inventory_value=None), LIMITS)
    assert "inventario total" in v.message and "no se puede calcular" in v.message


def test_peticion_de_otro_venue_es_error_de_programacion():
    with pytest.raises(ValueError):
        check(req(venue="nx"), ctx(), LIMITS)


@pytest.mark.spec("RF-10", "CA-04")
class TestOrdenYTextoDeLasViolaciones:
    """Fija el orden y el texto de cada violación (D-77: partir `check` no los cambia)."""

    def test_compra_con_casi_todo_mal(self):
        poco = {"USDT": Balance("USDT", D("100")), "BTCB2": Balance("BTCB2", D("0.1"))}
        context = ctx(book_ts=NOW - 20_000, open_orders=(own("sell", "1050"),), balances=poco)
        got = check(req("buy", qty="0.10005", price="1100.001"), context, LIMITS)
        assert [(v.code, v.message) for v in got] == [
            ("stale_data", "book sin actualizar hace más de 15 s"),
            (
                "order_size",
                "nocional 110.05510005 > 15 % del inventario total (400.00000000 USDT)"
                " = 60.00000000",
            ),
            ("tick", "precio 1100.001 fuera del tick 0.01"),
            ("lot", "cantidad 0.10005 fuera del lote 0.0001"),
            ("mid_deviation", "precio a 10.00% del mid 1000 (máx. 5%)"),
            ("self_cross", "compra a 1100.001 cruza tu venta abierta a 1050"),
            ("insufficient_balance", "hace falta 110.27521025 USDT, disponible 100.00000000"),
        ]

    def test_venta_con_casi_todo_mal(self):
        rico = {"USDT": Balance("USDT", D("600")), "BTCB2": Balance("BTCB2", D("0.1"))}
        market = replace(MARKET, min_qty=D("0.2"), min_notional=D("300"))
        context = ctx(market=market, open_orders=(own("buy", "950"),), balances=rico)
        got = check(req("sell", qty="0.15005", price="900.005"), context, LIMITS)
        assert [(v.code, v.message) for v in got] == [
            (
                "order_size",
                "nocional 135.04575025 > 15 % del inventario total (400.00000000 USDT)"
                " = 60.00000000",
            ),
            ("tick", "precio 900.005 fuera del tick 0.01"),
            ("lot", "cantidad 0.15005 fuera del lote 0.0001"),
            ("min_qty", "cantidad 0.15005 < mínimo 0.2 BTCB2"),
            ("min_notional", "nocional 135.04575025 < mínimo 300 USDT"),
            ("mid_deviation", "precio a 10.00% del mid 1000 (máx. 5%)"),
            ("self_cross", "venta a 900.005 cruza tu compra abierta a 950"),
            ("insufficient_balance", "hace falta 0.15005000 BTCB2, disponible 0.10000000"),
            ("venue_exposure", "valor en nk tras la orden 684.99575025 > tope 500 USDT"),
        ]

    def test_sin_mid_ni_inventario(self):
        """Sin mid no se mira ni la desviación ni la exposición."""
        rico = {"USDT": Balance("USDT", D("600")), "BTCB2": Balance("BTCB2", D("0.1"))}
        context = ctx(best_ask=None, inventory_value=None, balances=rico)
        got = check(req("buy", qty="0.01"), context, LIMITS)
        assert [(v.code, v.message) for v in got] == [
            ("stale_data", "sin mid fiable (book vacío, incompleto o cruzado)"),
            ("order_size", "no se puede calcular el inventario total para el tope del 15 %"),
        ]
