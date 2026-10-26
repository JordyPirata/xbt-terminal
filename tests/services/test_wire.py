"""Modelos ↔ JSON para el socket del daemon (T-52, RF-31, D-61): `Decimal` como
texto, sin `float` en dinero y de vuelta al mismo objeto."""

import json
from collections import deque
from decimal import Decimal

import pytest

from btcb2_terminal.core.bot import BotState
from btcb2_terminal.core.double import DoubleState, Leg
from btcb2_terminal.core.models import Balance, ConnState, D, Fill, Order, OrderRequest
from btcb2_terminal.services.account import VenueAccount
from btcb2_terminal.services.wire import decode, encode, loads


def round_trip(tp, obj):
    text = json.dumps(encode(obj))
    return decode(tp, json.loads(text))


@pytest.mark.spec("RF-31", "RNF-07")
class TestIdaYVuelta:
    def test_decimal_va_como_texto_exacto(self):
        assert encode(D("0.00000001")) == "0.00000001"
        assert round_trip(Decimal, D("0.10")) == D("0.10")

    def test_bot_state(self):
        st = BotState("halted", "pierna colgada", (1, 2), 20_000, D("0.0123"), 3, 2)
        assert round_trip(BotState, st) == st

    def test_doble_con_piernas(self):
        buy = OrderRequest("nk", "buy", D("0.01"), D("0.00725"), "t-1")
        sell = OrderRequest("nx", "sell", D("0.01"), D("0.00730"), "t-2")
        st = DoubleState(
            "d-1", Leg(buy, "filled", "o-1", D("0.01")), Leg(sell, "rejected", error="x"), 5, 7
        )
        assert round_trip(DoubleState, st) == st

    def test_cuenta_del_venue(self):
        conn = ConnState("nk", "private", "ws", "up", 12.5, 9)
        fill = Fill("nk", "f-1", "o-1", "buy", D("0.0072"), D("0.004"), D("6E-8"), "BTC", 5)
        acct = VenueAccount(
            "nk",
            conn=conn,
            conn_at=9,
            orders={
                "o-1": Order("nk", "o-1", None, "buy", D("0.0072"), D("0.01"), D("0"), "open", 1)
            },
            balances={"BTC": Balance("BTC", D("0.001"), D("0.0005"))},
            balances_at=10,
            recent_fills=deque([fill]),
            error=None,
        )
        back = round_trip(VenueAccount, acct)
        assert back.orders == acct.orders and back.balances == acct.balances
        assert back.conn == conn and back.fills == [fill]

    def test_contenedores(self):
        assert round_trip(dict[str, str], {"d-1": "venta"}) == {"d-1": "venta"}
        assert round_trip(list[OrderRequest], []) == []
        assert round_trip(int | None, None) is None


@pytest.mark.spec("RF-31")
class TestLineas:
    def test_una_linea_rara_es_error(self):
        with pytest.raises(ValueError):
            loads(b"no es json\n")
        with pytest.raises(ValueError):
            loads(b"[1, 2]\n")  # un mensaje es siempre un objeto

    def test_un_float_en_dinero_no_pasa(self):
        with pytest.raises((TypeError, ValueError)):
            decode(Balance, {"asset": "BTC", "available": 0.1, "locked": "0"})
