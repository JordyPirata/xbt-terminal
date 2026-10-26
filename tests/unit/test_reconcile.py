"""Candidatas para un envío incierto (T-25, RNF-12, PA-09, D-26)."""

import pytest

from xbt_terminal.core.models import D, Order, OrderRequest
from xbt_terminal.core.reconcile import WINDOW_MS, candidates

SENT = 1_792_530_500_000
REQ = OrderRequest("nx", "buy", D("0.01"), D("0.00725"), "t-abc123")


def order(oid="o-1", *, client_id=None, side="buy", qty="0.01", price="0.00725", ts=SENT,
          venue="nx", status="open"):  # fmt: skip
    return Order(venue, oid, client_id, side, D(price), D(qty), D("0"), status, ts)


@pytest.mark.spec("RNF-12")
class TestPorClientId:
    def test_solo_la_del_mismo_client_id(self):
        req = OrderRequest("nk", "buy", D("0.01"), D("0.00725"), "t-abc123")
        mine = order("a", client_id="t-abc123", venue="nk", ts=SENT - 10 * WINDOW_MS)
        other = order("b", client_id=None, venue="nk")
        assert candidates(req, [mine, other], SENT, frozenset(), by_client_id=True) == [mine]

    def test_una_conocida_al_enviar_no_cuenta(self):
        req = OrderRequest("nk", "buy", D("0.01"), D("0.00725"), "t-abc123")
        mine = order("a", client_id="t-abc123", venue="nk")
        assert candidates(req, [mine], SENT, frozenset({"a"}), by_client_id=True) == []


@pytest.mark.spec("RNF-12")
class TestSinClientId:
    def test_lado_precio_y_cantidad_iguales_en_la_ventana(self):
        same = order("a", qty="0.0100", price="0.007250", ts=SENT + 2_000)
        assert candidates(REQ, [same], SENT, frozenset(), by_client_id=False) == [same]

    @pytest.mark.parametrize(
        "kw",
        [
            {"side": "sell"},
            {"qty": "0.02"},
            {"price": "0.00726"},
            {"venue": "nk"},
            {"ts": SENT + WINDOW_MS + 1},
            {"ts": SENT - WINDOW_MS - 1},
        ],
    )
    def test_lo_que_no_coincide_no_cuenta(self, kw):
        assert candidates(REQ, [order(**kw)], SENT, frozenset(), by_client_id=False) == []

    def test_desfase_de_reloj_hacia_atras_dentro_de_la_ventana(self):
        early = order("a", ts=SENT - WINDOW_MS)
        assert candidates(REQ, [early], SENT, frozenset(), by_client_id=False) == [early]

    def test_varias_candidatas_se_devuelven_todas(self):
        a, b = order("a"), order("b", status="filled")
        assert candidates(REQ, [a, b], SENT, frozenset(), by_client_id=False) == [a, b]

    def test_la_misma_orden_dos_veces_cuenta_una(self):
        """Activas e historial pueden traer la misma: vale la última de la lista."""
        a, a_later = order("a"), order("a", status="filled", ts=SENT + 1)
        assert candidates(REQ, [a, a_later], SENT, frozenset(), by_client_id=False) == [a_later]
