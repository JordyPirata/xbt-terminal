"""Plan de reequilibrio (T-45, RF-29, CA-14): una transferencia de BTCB2 o de
BTC que deja los dos venues con el mismo % de BTCB2, con la comisión descontada
de lo enviado, su coste y si es viable."""

from decimal import Decimal

import pytest

from xbt_terminal.core.models import Balance, D, WithdrawalFee
from xbt_terminal.core.rebalance import Holding, break_even, holdings, plan

SAT = D("0.00000001")

# como el 2026-10-22: NonKYC casi todo en BTCB2, Neoxa casi todo en BTC
NK = Holding(btc=D("0.001"), btcb2=D("0.3"), btc_free=D("0.001"), btcb2_free=D("0.3"),
             price=D("0.0055"))  # fmt: skip
NX = Holding(btc=D("0.004"), btcb2=D("0.01"), btc_free=D("0.004"), btcb2_free=D("0.01"),
             price=D("0.0057"))  # fmt: skip


def fee(venue, asset, amount, minimum=None, active=True, on_top=None):
    """Como los venues: Neoxa cobra la comisión aparte; NonKYC, descontada (D-53)."""
    ticker = {"BTC": "BTC-MAIN", "BTCB2": "BTCB2"}[asset] if venue == "nk" else asset.lower()
    on_top = venue == "nx" if on_top is None else on_top
    return WithdrawalFee(
        venue, asset, ticker, D(amount), D(minimum) if minimum else None, active, on_top
    )


FEES = {
    ("nk", "BTC"): fee("nk", "BTC", "0.00005", "0.00001"),
    ("nk", "BTCB2"): fee("nk", "BTCB2", "0.0027"),
    ("nx", "BTC"): fee("nx", "BTC", "0.0001", "0.0005"),
    ("nx", "BTCB2"): fee("nx", "BTCB2", "0.0001", "0.01"),
}


def ratio(btc: Decimal, btcb2: Decimal, price: Decimal) -> Decimal:
    """Valor en BTCB2 sobre BTC: igual en los dos venues ⇔ mismo %."""
    return btcb2 * price / btc


def by_asset(p, asset):
    return next(t for t in p.transfers if t.asset == asset)


@pytest.mark.spec("RF-29", "CA-14")
class TestCantidad:
    def test_btcb2_del_venue_con_mas_porcentaje_iguala_al_satoshi(self):
        t = by_asset(plan({"nk": NK, "nx": NX}, FEES), "BTCB2")
        assert (t.source, t.dest) == ("nk", "nx")
        # NonKYC: comisión descontada (supuesto), sale lo pedido
        assert t.fee == D("0.0027") and t.received == t.amount - t.fee and t.debited == t.amount
        assert t.amount == t.amount.quantize(SAT)

        def gap(x):  # nk menos nx tras enviar x: cambia de signo en la cantidad justa
            a = ratio(NK.btc, NK.btcb2 - x, NK.price)
            b = ratio(NX.btc, NX.btcb2 + x - t.fee, NX.price)
            return a - b

        assert gap(t.amount) >= 0 > gap(t.amount + SAT)
        assert abs(t.after["nk"] - t.after["nx"]) < D("0.001")

    def test_btc_del_venue_con_menos_porcentaje_iguala_al_satoshi(self):
        t = by_asset(plan({"nk": NK, "nx": NX}, FEES), "BTC")
        assert (t.source, t.dest) == ("nx", "nk")
        # Neoxa: la comisión va aparte, llega lo pedido
        assert t.received == t.amount and t.debited == t.amount + D("0.0001")

        def gap(y):
            a = ratio(NK.btc + y, NK.btcb2, NK.price)
            b = ratio(NX.btc - y - t.fee, NX.btcb2, NX.price)
            return a - b

        assert gap(t.amount) >= 0 > gap(t.amount + SAT)
        assert abs(t.after["nk"] - t.after["nx"]) < D("0.01")  # 1 sat de BTC pesa más

    def test_el_origen_es_el_de_mas_porcentaje_aunque_cambie_el_orden(self):
        p = plan({"nk": NX, "nx": NK}, {(("nx" if v == "nk" else "nk"), a): f
                                         for (v, a), f in FEES.items()})  # fmt: skip
        assert (by_asset(p, "BTCB2").source, by_asset(p, "BTC").source) == ("nx", "nk")

    def test_porcentajes_antes_y_total(self):
        p = plan({"nk": NK, "nx": NX}, FEES)
        assert round(p.before["nk"], 2) == D("62.26")
        assert round(p.before["nx"], 2) == D("1.40")
        total = (D("0.3") * D("0.0055") + D("0.01") * D("0.0057")) / (
            D("0.005") + D("0.3") * D("0.0055") + D("0.01") * D("0.0057")
        ) * 100  # fmt: skip
        assert p.total_pct == total

    def test_iguales_no_hace_falta(self):
        p = plan({"nk": NK, "nx": NK}, FEES)
        assert all(not t.viable and "no hace falta" in t.problem for t in p.transfers)
        assert p.best is None


@pytest.mark.spec("RF-29", "CA-14")
class TestCoste:
    def test_coste_en_btc_al_mid_del_origen(self):
        p = plan({"nk": NK, "nx": NX}, FEES)
        assert by_asset(p, "BTCB2").cost_btc == D("0.0027") * NK.price
        assert by_asset(p, "BTC").cost_btc == D("0.0001")

    def test_recomienda_la_viable_mas_barata(self):
        p = plan({"nk": NK, "nx": NX}, FEES)
        assert p.best is by_asset(p, "BTCB2")  # 1 485 sats frente a 10 000

    def test_si_la_barata_no_es_viable_recomienda_la_otra(self):
        fees = {**FEES, ("nk", "BTCB2"): fee("nk", "BTCB2", "0.0027", active=False)}
        p = plan({"nk": NK, "nx": NX}, fees)
        assert p.best is by_asset(p, "BTC")

    def test_break_even(self):
        # 0.05 BTCB2 a 0.0055 con un 0.5 %: 0.000001375 BTC por doble
        assert break_even(D("0.00001485"), D("0.05"), D("0.0055"), D("0.5")) == 11
        assert break_even(D("0.000001375"), D("0.05"), D("0.0055"), D("0.5")) == 1
        assert break_even(D("0.0001"), D("0.05"), D("0.0055"), D("0")) is None


@pytest.mark.spec("RF-29", "CA-14")
class TestNoViable:
    def test_pasa_del_disponible(self):
        busy = Holding(NK.btc, NK.btcb2, NK.btc_free, D("0.05"), NK.price)  # resto en órdenes
        t = by_asset(plan({"nk": busy, "nx": NX}, FEES), "BTCB2")
        assert not t.viable and "disponible" in t.problem and "0.05" in t.problem

    def test_bajo_el_minimo(self):
        fees = {**FEES, ("nx", "BTC"): fee("nx", "BTC", "0.0001", "0.01")}
        t = by_asset(plan({"nk": NK, "nx": NX}, fees), "BTC")
        assert not t.viable and "mínimo" in t.problem and "0.01" in t.problem

    def test_la_comision_se_come_el_envio(self):
        # casi iguales: harían falta ~0.00185 BTCB2 y la comisión es 0.0027
        close = Holding(NK.btc, D("0.299"), NK.btc_free, D("0.299"), NK.price)
        t = by_asset(plan({"nk": NK, "nx": close}, FEES), "BTCB2")
        assert not t.viable and "comisión" in t.problem

    def test_retiro_cerrado(self):
        fees = {**FEES, ("nx", "BTC"): fee("nx", "BTC", "0.0001", active=False)}
        t = by_asset(plan({"nk": NK, "nx": NX}, fees), "BTC")
        assert not t.viable and "cerrado" in t.problem

    def test_sin_comision_se_calcula_con_cero_y_no_es_viable(self):
        fees = {**FEES, ("nk", "BTCB2"): "NonKYC: HTTP 500"}
        t = by_asset(plan({"nk": NK, "nx": NX}, fees), "BTCB2")
        assert not t.viable and "HTTP 500" in t.problem
        assert t.fee is None and t.cost_btc is None and t.received == t.amount > 0

    def test_sin_btc_en_ningun_venue(self):
        empty = {v: Holding(D(0), h.btcb2, D(0), h.btcb2_free, h.price)
                 for v, h in (("nk", NK), ("nx", NX))}  # fmt: skip
        p = plan(empty, FEES)
        assert p.best is None and all(not t.viable for t in p.transfers)


BAL = {
    "nk": {"BTC": Balance("BTC", D("0.001")), "BTCB2": Balance("BTCB2", D("0.2"), D("0.1"))},
    "nx": {"BTC": Balance("BTC", D("0.003"), D("0.001")), "BTCB2": Balance("BTCB2", D("0.01"))},
}


@pytest.mark.spec("RF-29")
class TestHoldings:
    def test_totales_para_el_porcentaje_y_disponibles_para_enviar(self):
        got = holdings(BAL, {"nk": D("0.0055"), "nx": D("0.0057")})
        assert got["nk"] == Holding(D("0.001"), D("0.3"), D("0.001"), D("0.2"), D("0.0055"))
        assert got["nx"] == Holding(D("0.004"), D("0.01"), D("0.003"), D("0.01"), D("0.0057"))

    def test_sin_saldos_o_sin_mid_lo_dice(self):
        assert "nx" in holdings({**BAL, "nx": None}, {"nk": D("1"), "nx": D("1")})
        assert "nk" in holdings(BAL, {"nk": None, "nx": D("1")})

    def test_hacen_falta_los_dos_venues(self):
        assert isinstance(plan({"nk": NK}, FEES), str)


@pytest.mark.spec("RF-29", "CA-14")
class TestComisionAparte:
    """Como el 2026-10-23: Neoxa con el BTCB2 y NonKYC sin él. La web de Neoxa
    pide 0.2, llegan 0.2 y descuenta 0.2001 (D-53)."""

    NX_FULL = Holding(D("0.00345193"), D("0.33280677"), D("0.00345193"), D("0.33280677"),
                      D("0.004061315"))  # fmt: skip
    NK_EMPTY = Holding(D("0.00261223"), D("0.00000708"), D("0.00261223"), D("0.00000708"),
                       D("0.00422892"))  # fmt: skip

    def test_btcb2_de_neoxa_llega_lo_pedido_y_sale_con_la_comision(self):
        t = by_asset(plan({"nk": self.NK_EMPTY, "nx": self.NX_FULL}, FEES), "BTCB2")
        assert (t.source, t.dest) == ("nx", "nk")
        assert t.amount == D("0.14002494")  # el recálculo hecho a mano ese día
        assert t.received == t.amount and t.debited == t.amount + D("0.0001")
        assert round(t.after["nx"], 2) == round(t.after["nk"], 2) == D("18.48")

    def test_el_disponible_cuenta_la_comision(self):
        # con lo justo para lo pedido pero no para la comisión, no es viable
        need = by_asset(plan({"nk": self.NK_EMPTY, "nx": self.NX_FULL}, FEES), "BTCB2").amount
        tight = Holding(self.NX_FULL.btc, self.NX_FULL.btcb2, self.NX_FULL.btc_free, need,
                        self.NX_FULL.price)  # fmt: skip
        t = by_asset(plan({"nk": self.NK_EMPTY, "nx": tight}, FEES), "BTCB2")
        assert not t.viable and "disponible" in t.problem

    def test_aparte_mayor_que_la_diferencia(self):
        fees = {**FEES, ("nk", "BTCB2"): fee("nk", "BTCB2", "0.0027", on_top=True)}
        close = Holding(NK.btc, D("0.299"), NK.btc_free, D("0.299"), NK.price)
        t = by_asset(plan({"nk": NK, "nx": close}, fees), "BTCB2")
        # la comisión sola ya pasa del medio: ninguna cantidad iguala
        assert t.amount == 0 and not t.viable and "mayor que lo que hay que mover" in t.problem
