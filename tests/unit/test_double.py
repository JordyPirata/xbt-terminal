"""Estado de una orden doble y pierna colgada (T-18, design.md §6.4, PA-08, D-15).

Pierna colgada: las cantidades llenas de las dos piernas difieren y la que va
por detrás está rechazada o cancelada, o lleva más de `hanging_leg_after_s`
sin igualarse."""

from dataclasses import replace

import pytest

from xbt_terminal.core.double import DoubleState, Leg, hanging, on_order, start
from xbt_terminal.core.models import D, Order, OrderRequest

T0 = 1_792_497_600_000
AFTER_MS = 30_000


def double(**kw) -> DoubleState:
    buy = OrderRequest("nk", "buy", D("0.01"), D("0.0072"), "t-b")
    sell = OrderRequest("nx", "sell", D("0.01"), D("0.0073"), "t-s")
    state = start("d-1", buy, sell, T0)
    return replace(state, **kw)


def sent(state, buy="o-b", sell="o-s") -> DoubleState:
    """Respuestas del envío: traen el client_id de cada pierna."""
    state = on_order(state, order("nk", buy, "buy", cid="t-b"), T0)
    return on_order(state, order("nx", sell, "sell", cid="t-s"), T0)


def order(venue, oid, side, filled="0", status="open", cid=None):
    return Order(venue, oid, cid, side, D("0.0072"), D("0.01"), D(filled), status, T0)


@pytest.mark.spec("RF-07")
class TestPiernas:
    def test_al_empezar_las_dos_estan_pendientes(self):
        s = double()
        assert (s.buy.status, s.sell.status) == ("pending", "pending")
        assert (s.buy.order_id, s.sell.order_id) == (None, None)

    def test_la_respuesta_del_envio_asigna_la_orden_por_client_id(self):
        s = on_order(double(), order("nk", "o-b", "buy", cid="t-b"), T0)
        assert (s.buy.order_id, s.buy.status) == ("o-b", "open")
        assert s.sell.order_id is None

    def test_las_actualizaciones_van_por_order_id(self):
        s = sent(double())
        s = on_order(s, order("nx", "o-s", "sell", filled="0.004", status="partial"), T0 + 5)
        assert (s.sell.status, s.sell.filled) == ("partial", D("0.004"))
        assert s.buy.filled == 0

    def test_una_orden_ajena_no_cambia_nada(self):
        s = sent(double())
        assert on_order(s, order("nk", "otra", "buy", filled="0.01", status="filled"), T0) == s

    def test_mismo_id_en_otro_venue_no_cuenta(self):
        s = sent(double())
        assert on_order(s, order("nx", "o-b", "sell", filled="0.01", status="filled"), T0) == s

    def test_fallo_al_enviar(self):
        s = double().fail("sell", "PRICE_OUT_OF_BAND")
        assert (s.sell.status, s.sell.error) == ("rejected", "PRICE_OUT_OF_BAND")

    def test_terminada_cuando_las_dos_estan_terminadas(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.01", status="filled"), T0)
        assert not s.done
        s = on_order(s, order("nx", "o-s", "sell", filled="0.01", status="filled"), T0)
        assert s.done and hanging(s, T0 + 999_999, AFTER_MS) is None


@pytest.mark.spec("RF-07", "CA-03")
class TestColgada:
    def test_sin_fills_no_hay_desequilibrio(self):
        s = sent(double())
        assert s.imbalance_since is None
        assert hanging(s, T0 + 10 * AFTER_MS, AFTER_MS) is None

    def test_una_llena_y_la_otra_abierta_mas_de_n_segundos(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.01", status="filled"), T0 + 1_000)
        assert s.imbalance_since == T0 + 1_000
        assert hanging(s, T0 + 1_000 + AFTER_MS, AFTER_MS) is None  # el límite exacto aún no
        reason = hanging(s, T0 + 1_001 + AFTER_MS, AFTER_MS)
        assert reason is not None and "Neoxa" in reason and "30 s" in reason

    def test_parcial_tambien_cuenta(self):
        s = sent(double())
        s = on_order(s, order("nx", "o-s", "sell", filled="0.002", status="partial"), T0)
        assert hanging(s, T0 + AFTER_MS + 1, AFTER_MS) is not None

    def test_si_se_igualan_se_reinicia_el_reloj(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.005", status="partial"), T0)
        s = on_order(s, order("nx", "o-s", "sell", filled="0.005", status="partial"), T0 + 20_000)
        assert s.imbalance_since is None
        s = on_order(s, order("nk", "o-b", "buy", filled="0.01", status="filled"), T0 + 25_000)
        assert s.imbalance_since == T0 + 25_000
        assert hanging(s, T0 + 25_000 + AFTER_MS, AFTER_MS) is None

    def test_mas_fills_sin_igualar_no_reinician_el_reloj(self):
        """Una pierna que se llena a trozos sin que la otra la siga sigue colgándose."""
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.002", status="partial"), T0)
        s = on_order(s, order("nk", "o-b", "buy", filled="0.006", status="partial"), T0 + 20_000)
        assert s.imbalance_since == T0
        assert hanging(s, T0 + AFTER_MS + 1, AFTER_MS) is not None

    def test_una_llena_y_la_otra_rechazada_es_inmediato(self):
        s = on_order(
            double(), order("nk", "o-b", "buy", filled="0.01", status="filled", cid="t-b"), T0
        )
        s = s.fail("sell", "Insufficient funds")
        reason = hanging(s, T0, AFTER_MS)
        assert reason is not None and "rechazada" in reason

    def test_una_parcial_y_la_otra_cancelada(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.003", status="partial"), T0)
        s = on_order(s, order("nx", "o-s", "sell", status="cancelled"), T0 + 1)
        assert "cancelada" in (hanging(s, T0 + 1, AFTER_MS) or "")

    def test_rechazada_sin_fills_en_la_otra_no_es_colgada(self):
        """PA-08: hace falta una pierna con fills."""
        s = on_order(double(), order("nk", "o-b", "buy", cid="t-b"), T0).fail("sell", "x")
        assert hanging(s, T0 + 10 * AFTER_MS, AFTER_MS) is None

    def test_la_que_va_por_detras_es_la_colgada(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.004", status="partial"), T0)
        s = on_order(s, order("nx", "o-s", "sell", filled="0.01", status="filled"), T0)
        reason = hanging(s, T0 + AFTER_MS + 1, AFTER_MS)
        assert reason is not None and reason.startswith("compra en NonKYC")


def test_leg_es_inmutable():
    leg = Leg(OrderRequest("nk", "buy", D("1"), D("1"), "t"))
    with pytest.raises(AttributeError):
        leg.status = "open"  # type: ignore[misc]
