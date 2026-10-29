"""Modelos ↔ JSON para el socket del daemon (T-52, RF-31, D-61): `Decimal` como
texto, sin `float` en dinero y de vuelta al mismo objeto."""

import dataclasses
import json
from collections import deque
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

import pytest

from xbt_terminal.core.bot import BotState
from xbt_terminal.core.double import DoubleState, Leg
from xbt_terminal.core.models import Balance, ConnState, D, Fill, Order, OrderRequest
from xbt_terminal.services.account import VenueAccount
from xbt_terminal.services.wire import decode, encode, loads


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


@dataclasses.dataclass
class Sample:
    """Dataclass de prueba: valor por defecto, deque con `maxlen` y campo sin init."""

    a: int
    b: str = "x"
    c: deque[int] = dataclasses.field(default_factory=lambda: deque(maxlen=2))
    d: int = dataclasses.field(default=7, init=False)


@pytest.mark.spec("RF-31", "RNF-07")
class TestCadaTipo:
    """Fija qué devuelve `decode` con cada familia de tipos (D-77: partirla no lo cambia)."""

    @pytest.mark.parametrize(
        ("tp", "data", "expected"),
        [
            (Any, {"x": 1}, {"x": 1}),
            (type(None), None, None),
            (int | None, None, None),
            (int | str, "a", "a"),
            (Literal["a", "b"], "a", "a"),
            (Decimal, "1.5", D("1.5")),
            (dict[str, int], {"k": 1}, {"k": 1}),
            (Mapping[str, int], {"k": 1}, {"k": 1}),
            (list[int], [1, 2], [1, 2]),
            (tuple[int, str], [1, "a"], (1, "a")),
            (tuple[int, ...], [1, 2], (1, 2)),
            (set[int], [1, 1], {1}),
            (frozenset[int], [2], frozenset({2})),
            (int, 1, 1),
            (bool, True, True),
            (float, 1.5, 1.5),
        ],
    )
    def test_valores(self, tp, data, expected):
        got = decode(tp, data)
        assert got == expected and type(got) is type(expected)

    def test_deque_y_float_desde_entero(self):
        assert decode(deque[int], [1]) == deque([1])
        assert type(decode(deque[int], [1])) is deque
        assert type(decode(float, 1)) is float

    def test_dataclass(self):
        assert decode(Sample, {"a": 1}) == Sample(1)
        got = decode(Sample, {"a": 1, "c": [1, 2, 3], "d": 9, "z": 0})
        # conserva el maxlen del campo; ignora claves de más y campos sin init
        assert got.c == deque([2, 3]) and got.c.maxlen == 2 and got.d == 7

    @pytest.mark.parametrize(
        ("tp", "data", "error", "message"),
        [
            (type(None), 0, TypeError, "se esperaba null y llegó 0"),
            (
                int | Decimal,
                1.5,
                TypeError,
                "1.5 no encaja en int | decimal.Decimal: se esperaba int y llegó 1.5;"
                " un Decimal llega como texto, no float",
            ),
            (
                int | None,
                "x",
                TypeError,
                "'x' no encaja en int | None: se esperaba int y llegó 'x'",
            ),
            (Literal["a", "b"], "c", ValueError, "'c' no es uno de ('a', 'b')"),
            (Decimal, 1, TypeError, "un Decimal llega como texto, no int"),
            (Decimal, "abc", ValueError, "Decimal inválido: 'abc'"),
            (Sample, [1], TypeError, "Sample llega como objeto"),
            (Sample, {"a": "1"}, TypeError, "se esperaba int y llegó '1'"),
            (dict[str, int], [], TypeError, "se esperaba un objeto"),
            (list[int], {}, TypeError, "se esperaba una lista"),
            (int, True, TypeError, "se esperaba int y llegó True"),
            (int, "1", TypeError, "se esperaba int y llegó '1'"),
            (bool, 1, TypeError, "se esperaba bool y llegó 1"),
            (str, 1, TypeError, "se esperaba str y llegó 1"),
            (float, True, TypeError, "se esperaba float y llegó True"),
            (bytes, b"", TypeError, "tipo sin decodificar: <class 'bytes'>"),
        ],
    )
    def test_errores(self, tp, data, error, message):
        with pytest.raises(error) as exc:
            decode(tp, data)
        assert type(exc.value) is error and str(exc.value) == message

    def test_tupla_de_largo_fijo_con_menos_elementos(self):
        with pytest.raises(ValueError):
            decode(tuple[int, str], [1])
