"""Códec Engine.IO 4 / Socket.IO 5 con frames reales del WS de Neoxa (2026-10-20)."""

from decimal import Decimal

import pytest

from btcb2_terminal.adapters import socketio as sio
from btcb2_terminal.adapters.socketio import Packet, ProtocolError

NS = "/exchange"


@pytest.mark.spec("RF-01")
class TestDecode:
    def test_open_con_tiempos_de_ping(self):
        p = sio.decode(
            '0{"sid":"QWxX_Ogr7_kTyTbcAbAv","upgrades":[],"pingInterval":25000,'
            '"pingTimeout":20000,"maxPayload":1000000}'
        )
        assert p.kind == "open"
        assert p.data["pingInterval"] == 25000 and p.data["pingTimeout"] == 20000

    def test_ping_y_close_de_engine(self):
        assert sio.decode("2") == Packet("ping")
        assert sio.decode("1") == Packet("close")

    def test_connect_del_namespace(self):
        p = sio.decode('40/exchange,{"sid":"ohpzJ6SVqtZre8MzAbAx"}')
        assert p == Packet("connect", NS, data={"sid": "ohpzJ6SVqtZre8MzAbAx"})

    def test_connect_namespace_raiz(self):
        assert sio.decode("40") == Packet("connect", "/")

    def test_evento_con_floats_como_decimal(self):
        p = sio.decode(
            '42/exchange,["exchange:orderbook",{"pair":"BTCB2_BTC","bids":'
            '[{"price":0.00713454,"quantity":0.12668383,"isAmm":true}],"asks":[]}]'
        )
        assert (p.kind, p.namespace, p.event) == ("event", NS, "exchange:orderbook")
        level = p.data["bids"][0]
        assert level["price"] == Decimal("0.00713454")
        assert isinstance(level["quantity"], Decimal)

    def test_evento_sin_argumentos(self):
        p = sio.decode('42/exchange,["pong"]')
        assert (p.kind, p.event, p.data) == ("event", "pong", None)

    def test_evento_con_id_de_ack(self):
        p = sio.decode('42/exchange,7["x",{"a":1}]')
        assert (p.event, p.data) == ("x", {"a": 1})

    def test_disconnect_y_connect_error(self):
        assert sio.decode("41/exchange,").kind == "disconnect"
        p = sio.decode('44/exchange,{"message":"Invalid namespace"}')
        assert p.kind == "connect_error" and p.data == {"message": "Invalid namespace"}

    @pytest.mark.parametrize("frame", ["", "9", "4", "47/exchange,", '42/exchange,{"no":"lista"}'])
    def test_frame_invalido(self, frame):
        with pytest.raises(ProtocolError):
            sio.decode(frame)

    def test_json_roto(self):
        with pytest.raises(ProtocolError):
            sio.decode('42/exchange,["exchange:trade",{')


@pytest.mark.spec("RF-01")
class TestEncode:
    def test_connect(self):
        assert sio.connect_frame(NS) == "40/exchange,"

    def test_evento(self):
        frame = sio.event_frame(NS, "subscribe", {"pair": "BTCB2_BTC"})
        assert frame == '42/exchange,["subscribe",{"pair":"BTCB2_BTC"}]'

    def test_evento_sin_datos(self):
        assert sio.event_frame(NS, "ping") == '42/exchange,["ping"]'

    def test_ida_y_vuelta(self):
        p = sio.decode(sio.event_frame(NS, "subscribe", {"pair": "BTCB2_BTC"}))
        assert (p.event, p.data) == ("subscribe", {"pair": "BTCB2_BTC"})

    def test_pong(self):
        assert sio.PONG == "3"
