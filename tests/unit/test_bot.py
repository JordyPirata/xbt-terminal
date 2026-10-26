"""Decisión pura del bot y su máquina de estados (T-35, RF-19, RF-20, RF-22, RNF-22)."""

from dataclasses import replace

import pytest

from xbt_terminal.core.bot import (
    DAY_MS,
    HOUR_MS,
    BotLimits,
    BotState,
    FeeToken,
    Fire,
    Skip,
    VenueView,
    arm,
    capped,
    decide,
    disarm,
    finished,
    fired,
    price_move_pct,
    rejected,
    tick,
    token_fee,
)
from xbt_terminal.core.models import D, Level

from ..fakes import spec

NOW = 1_800_000_000_000
LIMITS = BotLimits(
    min_net_pct=D("0.5"),
    max_slippage_pct=D("0.2"),
    max_qty=D("100"),
    fill_timeout_ms=3000,
    cooldown_s=10,
    max_trades_per_hour=20,
    max_daily_btc=D("1"),
    max_consecutive_failures=3,
)
ARMED = arm(BotState())


def view(venue, bids=(), asks=(), quote="1", base="1000", blocked=None):
    return VenueView(
        spec=spec(venue),
        bids=tuple(Level(D(p), D(q)) for p, q in bids),
        asks=tuple(Level(D(p), D(q)) for p, q in asks),
        quote_available=D(quote),
        base_available=D(base),
        blocked=blocked,
    )


def crossed(nk=None, nx=None):
    """Comprar en NonKYC a 0.00700000 y vender en Neoxa a 0.00720000."""
    nk_asks = [("0.00700000", "50"), ("0.00702000", "100")]
    nx_bids = [("0.00720000", "80"), ("0.00718000", "100")]
    views = {
        "nk": view("nk", bids=[("0.00690000", "40")], asks=nk_asks),
        "nx": view("nx", bids=nx_bids, asks=[("0.00730000", "9")]),
    }
    if nk:
        views["nk"] = replace(views["nk"], **nk)
    if nx:
        views["nx"] = replace(views["nx"], **nx)
    return views


@pytest.mark.spec("RF-19", "RF-20", "RNF-22", "CA-08")
class TestDisparo:
    def test_cruce_por_encima_del_umbral(self):
        fire = decide(crossed(), ARMED, LIMITS, NOW)
        assert isinstance(fire, Fire)
        assert (fire.buy_venue, fire.sell_venue) == ("nk", "nx")
        # límites agresivas: 0.007 × 1.002 hacia arriba, 0.0072 × 0.998 hacia abajo
        assert fire.buy_price == D("0.00701400") and fire.sell_price == D("0.00718560")
        # profundidad hasta esos precios: 50 en NonKYC, 80 en Neoxa
        assert fire.qty == D("50.0000")
        unit = D("0.00718560") * D("0.998") - D("0.00701400") * D("1.002")
        assert fire.net == unit * 50
        assert fire.net_pct == unit / D("0.00701400") * 100

    def test_por_debajo_del_umbral(self):
        skip = decide(crossed(), ARMED, replace(LIMITS, min_net_pct=D("3")), NOW)
        assert isinstance(skip, Skip) and skip.reason == "below_threshold"
        assert "2.04" in skip.detail

    def test_la_neta_usa_los_precios_limite_no_el_mejor_precio(self):
        # con el mejor precio la neta es 2.45 %, con un 1 % de slippage por lado < 0.5 %
        skip = decide(crossed(), ARMED, replace(LIMITS, max_slippage_pct=D("1")), NOW)
        assert isinstance(skip, Skip) and skip.reason == "below_threshold"

    def test_tick_hacia_el_lado_seguro(self):
        views = crossed(
            nk={"asks": (Level(D("0.00700001"), D("50")),)},
            nx={"bids": (Level(D("0.00720001"), D("80")),)},
        )
        fire = decide(views, ARMED, LIMITS, NOW)
        # 0.00701401002 → arriba; 0.00718560998 → abajo
        assert fire.buy_price == D("0.00701402") and fire.sell_price == D("0.00718560")

    def test_sentido_contrario(self):
        views = {
            "nk": view("nk", bids=[("0.00720000", "30")], asks=[("0.00730000", "5")]),
            "nx": view("nx", bids=[("0.00690000", "5")], asks=[("0.00700000", "60")]),
        }
        fire = decide(views, ARMED, LIMITS, NOW)
        assert (fire.buy_venue, fire.sell_venue, fire.qty) == ("nx", "nk", D("30.0000"))

    def test_la_profundidad_suma_los_niveles_hasta_el_limite(self):
        views = crossed(
            nk={"asks": (Level(D("0.00700000"), D("20")), Level(D("0.00701000"), D("15")))}
        )
        assert decide(views, ARMED, LIMITS, NOW).qty == D("35.0000")


@pytest.mark.spec("RF-20")
class TestCantidad:
    @pytest.mark.parametrize(
        ("change", "qty"),
        [
            ({"nx": {"bids": (Level(D("0.00720000"), D("10")),)}}, "10.0000"),  # profundidad venta
            # 0.1 BTC / (0.007014 × 1.002) = 14.22873… al lote de NonKYC
            ({"nk": {"quote_available": D("0.1")}}, "14.2287"),
            ({"nx": {"base_available": D("7.5")}}, "7.5000"),  # BTCB2 en el venue de venta
        ],
    )
    def test_el_minimo_de_cada_termino(self, change, qty):
        assert decide(crossed(**change), ARMED, LIMITS, NOW).qty == D(qty)

    def test_max_qty(self):
        assert decide(crossed(), ARMED, replace(LIMITS, max_qty=D("5")), NOW).qty == D("5.0000")

    def test_lo_que_queda_del_tope_diario(self):
        state = replace(ARMED, day=NOW // DAY_MS, day_btc=D("0.9"))
        # 0.1 BTC de compra / 0.007014 = 14.257… → 14.2571
        assert decide(crossed(), state, LIMITS, NOW).qty == D("14.2571")

    def test_sin_saldo_no_dispara(self):
        skip = decide(crossed(nx={"base_available": D("0")}), ARMED, LIMITS, NOW)
        assert isinstance(skip, Skip) and skip.reason == "no_balance"

    def test_bajo_el_lote_no_dispara(self):
        views = crossed(nk={"asks": (Level(D("0.00700000"), D("0.00005")),)})
        skip = decide(views, ARMED, LIMITS, NOW)
        assert isinstance(skip, Skip) and skip.reason == "no_depth"

    def test_bajo_el_nocional_minimo(self):
        nk = {"spec": replace(spec("nk"), min_notional=D("1"))}
        skip = decide(crossed(nk=nk), ARMED, LIMITS, NOW)
        assert isinstance(skip, Skip) and skip.reason == "min_notional"


@pytest.mark.spec("RF-19", "RF-22")
class TestNoDispara:
    def test_sin_armar(self):
        for state in (BotState(), replace(ARMED, status="halted"), replace(ARMED, status="firing")):
            assert decide(crossed(), state, LIMITS, NOW) == Skip("not_armed", state.status)

    def test_venue_bloqueado(self):
        skip = decide(crossed(nx={"blocked": "book viejo"}), ARMED, LIMITS, NOW)
        assert skip == Skip("blocked", "nx: book viejo")

    def test_sin_precios(self):
        skip = decide(crossed(nk={"asks": (), "bids": ()}), ARMED, LIMITS, NOW)
        assert isinstance(skip, Skip) and skip.reason == "no_quotes"

    def test_cooldown(self):
        state = replace(ARMED, last_fire_at=NOW - 9_000)
        assert decide(crossed(), state, LIMITS, NOW).reason == "cooldown"
        state = replace(ARMED, last_fire_at=NOW - 10_000)
        assert isinstance(decide(crossed(), state, LIMITS, NOW), Fire)

    def test_tope_por_hora(self):
        recent = tuple(NOW - 60_000 * i for i in range(1, 21))
        assert decide(crossed(), replace(ARMED, fired_at=recent), LIMITS, NOW).reason == "hour_cap"
        old = (*recent[:-1], NOW - HOUR_MS)  # uno ya tiene una hora
        assert isinstance(decide(crossed(), replace(ARMED, fired_at=old), LIMITS, NOW), Fire)

    def test_tope_diario_gastado(self):
        state = replace(ARMED, day=NOW // DAY_MS, day_btc=D("1"))
        assert decide(crossed(), state, LIMITS, NOW).reason == "day_cap"

    def test_el_tope_diario_es_del_dia_utc(self):
        state = replace(ARMED, day=NOW // DAY_MS - 1, day_btc=D("1"))  # era de ayer
        assert isinstance(decide(crossed(), state, LIMITS, NOW), Fire)


@pytest.mark.spec("RF-18", "RF-21", "RF-22")
class TestEstados:
    def fire(self):
        fire = decide(crossed(), ARMED, LIMITS, NOW)
        assert isinstance(fire, Fire)
        return fire

    def test_armar_y_parar(self):
        state = arm(replace(BotState(), status="halted", reason="x", failures=2))
        assert (state.status, state.reason, state.failures) == ("armed", None, 0)
        assert disarm(state).status == "off"

    def test_disparo_cuenta(self):
        fire = self.fire()
        state = fired(ARMED, fire, NOW)
        assert state.status == "firing" and state.last_fire_at == NOW
        assert state.fired_at == (NOW,)
        assert state.day == NOW // DAY_MS and state.day_btc == fire.qty * fire.buy_price

    def test_disparo_de_otro_dia_reinicia_el_gasto(self):
        old = replace(ARMED, day=NOW // DAY_MS - 1, day_btc=D("0.5"))
        fire = self.fire()
        assert fired(old, fire, NOW).day_btc == fire.qty * fire.buy_price

    def test_termina_bien_y_enfria(self):
        state = finished(fired(ARMED, self.fire(), NOW), LIMITS, NOW + 1_000, error=None)
        assert (state.status, state.failures) == ("cooldown", 0)
        assert tick(state, LIMITS, NOW + 9_999).status == "cooldown"
        assert tick(state, LIMITS, NOW + 10_000).status == "armed"

    def test_termina_mal_y_se_para(self):
        state = finished(fired(ARMED, self.fire(), NOW), LIMITS, NOW, error="hanging_leg: x")
        assert (state.status, state.reason) == ("halted", "hanging_leg: x")
        assert tick(state, LIMITS, NOW + HOUR_MS).status == "halted"  # solo a mano

    def test_al_llegar_al_tope_diario_se_para(self):
        state = replace(fired(ARMED, self.fire(), NOW), day_btc=D("1"))
        state = finished(state, LIMITS, NOW, error=None)
        assert state.status == "halted" and state.reason.startswith("day_cap")

    def test_tope_diario_sin_disparo_se_para(self):
        """T-46 (D-54): con el tope ya gastado al evaluar, `halted` como al agotarlo."""
        state = capped(replace(ARMED, failures=2), LIMITS)
        assert (state.status, state.failures) == ("halted", 0)
        assert state.reason == f"day_cap: {LIMITS.max_daily_btc} BTC de compra hoy"
        assert tick(state, LIMITS, NOW + HOUR_MS).status == "halted"  # solo a mano

    def test_rechazos_seguidos(self):
        state = fired(ARMED, self.fire(), NOW)
        state = rejected(state, LIMITS, "self_cross")
        assert (state.status, state.failures) == ("armed", 1)
        state = rejected(rejected(state, LIMITS, "self_cross"), LIMITS, "stale_data")
        assert state.status == "halted" and "3 disparos rechazados" in state.reason

    def test_un_exito_reinicia_los_fallos(self):
        state = rejected(fired(ARMED, self.fire(), NOW), LIMITS, "x")
        state = finished(fired(state, self.fire(), NOW), LIMITS, NOW, error=None)
        assert state.failures == 0

    def test_tick_olvida_disparos_de_hace_mas_de_una_hora(self):
        state = replace(ARMED, fired_at=(NOW - HOUR_MS - 1, NOW - 5))
        assert tick(state, LIMITS, NOW).fired_at == (NOW - 5,)


def amm_steps(nx_bids=(("0.00460000", "5"),)):
    """Escalones del pool AMM de NonKYC (D-60): 0.0018 BTCB2 cada uno, con el
    mínimo del venue en 0.0000126 BTC (≈ 0.0029 BTCB2, dos escalones)."""
    nk_spec = replace(spec("nk"), min_notional=D("0.0000126"))
    asks = (("0.00447270", "0.0018"), ("0.00448768", "0.0017"), ("0.00450014", "0.0018"))
    return {
        "nk": replace(view("nk", bids=[("0.00445000", "1")], asks=asks), spec=nk_spec),
        "nx": view("nx", bids=nx_bids, asks=[("0.00470000", "5")]),
    }


@pytest.mark.spec("RF-20")
class TestRecorrerElBook:
    def test_sin_walk_el_mejor_escalon_no_llega_al_minimo(self):
        skip = decide(amm_steps(), ARMED, LIMITS, NOW)
        assert isinstance(skip, Skip) and skip.reason == "min_notional"

    def test_con_walk_toma_los_escalones_que_rinden_mas(self):
        limits = replace(LIMITS, max_walk_pct={"nk": D("0.8")})
        fire = decide(amm_steps(), ARMED, limits, NOW)
        assert isinstance(fire, Fire) and (fire.buy_venue, fire.sell_venue) == ("nk", "nx")
        # referencia: 3.er escalón; 0.00450014 × 1.002 = 0.00450914028 → arriba
        assert fire.buy_price == D("0.00450915") and fire.qty == D("0.0053")
        assert fire.sell_price == D("0.00459080")

    def test_no_paga_profundidad_que_no_puede_vender(self):
        # en Neoxa solo caben 0.0035 al mejor bid: el 2.º escalón basta y sale más barato
        views = amm_steps(nx_bids=(("0.00460000", "0.0035"), ("0.00440000", "10")))
        fire = decide(views, ARMED, replace(LIMITS, max_walk_pct={"nk": D("0.8")}), NOW)
        assert fire.buy_price == D("0.00449666") and fire.qty == D("0.0035")

    def test_si_el_escalon_mas_hondo_no_da_el_umbral_se_queda_en_el_anterior(self):
        # neta: 2.º escalón ≈ 1.69 %, 3.º ≈ 1.41 %
        limits = replace(LIMITS, max_walk_pct={"nk": D("0.8")}, min_net_pct=D("1.5"))
        fire = decide(amm_steps(), ARMED, limits, NOW)
        assert fire.buy_price == D("0.00449666") and fire.qty == D("0.0035")

    def test_el_tope_corta_el_recorrido(self):
        # 0.00447270 × 1.004 = 0.00449059: entra el 2.º escalón, no el 3.º
        limits = replace(LIMITS, max_walk_pct={"nk": D("0.4")})
        fire = decide(amm_steps(), ARMED, limits, NOW)
        assert fire.buy_price == D("0.00449666") and fire.qty == D("0.0035")

    def test_el_walk_de_un_venue_no_toca_el_otro(self):
        limits = replace(LIMITS, max_walk_pct={"nx": D("5")})
        skip = decide(amm_steps(), ARMED, limits, NOW)
        assert isinstance(skip, Skip) and skip.reason == "min_notional"

    def test_con_profundidad_pero_sin_neta_el_motivo_es_el_umbral(self):
        limits = replace(LIMITS, max_walk_pct={"nk": D("0.8")}, min_net_pct=D("2"))
        skip = decide(amm_steps(), ARMED, limits, NOW)
        assert isinstance(skip, Skip) and skip.reason == "below_threshold"
        assert "1.69" in skip.detail

    def test_walk_cero_es_lo_de_siempre(self):
        limits = replace(LIMITS, max_walk_pct={"nk": D("0"), "nx": D("0")})
        assert decide(crossed(), ARMED, limits, NOW) == decide(crossed(), ARMED, LIMITS, NOW)


def thin(nk_fee=None):
    """Comprar a 0.007 y vender a 0.00705, sin slippage: con fee 0.2 % en las dos
    piernas la neta es 0.31 % (< 0.33 %); con 0.15 % en NonKYC, 0.36 %."""
    limits = replace(LIMITS, max_slippage_pct=D("0"), min_net_pct=D("0.33"))
    views = {
        "nk": view("nk", bids=[("0.00690000", "40")], asks=[("0.00700000", "50")]),
        "nx": view("nx", bids=[("0.00705000", "80")], asks=[("0.00730000", "9")]),
    }
    views["nk"] = replace(views["nk"], net_fee=nk_fee)
    return views, limits


@pytest.mark.spec("RF-19", "RF-24")
class TestFeeConToken:
    def test_sin_descuento_no_llega(self):
        views, limits = thin()
        result = decide(views, ARMED, limits, NOW)
        assert isinstance(result, Skip) and result.reason == "below_threshold"

    def test_con_descuento_dispara_con_esa_fee(self):
        views, limits = thin(nk_fee=D("0.0015"))
        fire = decide(views, ARMED, limits, NOW)
        assert isinstance(fire, Fire)
        unit = D("0.00705000") * D("0.998") - D("0.00700000") * D("1.0015")
        assert fire.net == unit * fire.qty

    def test_la_cantidad_sigue_con_la_fee_completa(self):
        views, limits = thin(nk_fee=D("0.0015"))
        views["nk"] = replace(views["nk"], quote_available=D("0.07"))
        fire = decide(views, ARMED, limits, NOW)
        assert isinstance(fire, Fire)
        assert fire.qty == spec("nk").quantize_qty(D("0.07") / (D("0.007") * D("1.002")))


@pytest.mark.spec("RF-24")
class TestTokenFee:
    TOKEN = FeeToken("NKYC", D("1"), D("25"))

    def test_con_saldo_suficiente(self):
        assert token_fee(D("0.002"), self.TOKEN, D("1")) == D("0.0015")

    @pytest.mark.parametrize("available", [D("0.99"), D("0"), None])
    def test_bajo_el_minimo_o_sin_saldo(self, available):
        assert token_fee(D("0.002"), self.TOKEN, available) is None


@pytest.mark.spec("RF-19", "RNF-22", "CA-08")
class TestMovimientoBrusco:
    """D-71: tras un salto del mid, otros bots llegan antes; el bot no dispara."""

    def test_sin_filtro_dispara_aunque_se_mueva(self):
        views = crossed(nx={"move_pct": D("5")})
        assert isinstance(decide(views, ARMED, LIMITS, NOW), Fire)

    def test_con_filtro_y_salto_no_dispara(self):
        limits = replace(LIMITS, max_move_pct=D("1"), move_window_ms=2000)
        views = crossed(nx={"move_pct": D("2.8")})
        got = decide(views, ARMED, limits, NOW)
        assert isinstance(got, Skip) and got.reason == "volatile"
        assert got.detail == "nx: mid 2.80 % en 2 s > 1 %"

    def test_con_filtro_y_calma_dispara(self):
        limits = replace(LIMITS, max_move_pct=D("1"))
        views = crossed(nk={"move_pct": D("1")}, nx={"move_pct": D("0.3")})
        assert isinstance(decide(views, ARMED, limits, NOW), Fire)  # justo en el tope

    def test_sin_medida_no_frena(self):
        limits = replace(LIMITS, max_move_pct=D("1"))
        assert isinstance(decide(crossed(), ARMED, limits, NOW), Fire)

    def test_el_primero_en_orden_de_venue(self):
        limits = replace(LIMITS, max_move_pct=D("1"))
        views = crossed(nk={"move_pct": D("3")}, nx={"move_pct": D("4")})
        assert decide(views, ARMED, limits, NOW).detail.startswith("nk:")

    def test_movimiento_de_una_ventana(self):
        assert price_move_pct([D("0.0038"), D("0.0037"), D("0.00374")]) == (
            (D("0.0038") - D("0.0037")) / D("0.0037") * 100
        )
        assert price_move_pct([D("0.004")]) == 0
        assert price_move_pct([]) is None
