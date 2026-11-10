"""Engine.IO 4 / Socket.IO 5 codec with real frames of Neoxa's WS (2026-10-20)."""

from decimal import Decimal

import pytest

from xbt_terminal.adapters import socketio as sio
from xbt_terminal.adapters.socketio import Packet, ProtocolError

NS = "/exchange"


@pytest.mark.spec("RF-01")
class TestDecode:
    def test_open_with_ping_times(self):
        packet = sio.decode(
            '0{"sid":"QWxX_Ogr7_kTyTbcAbAv","upgrades":[],"pingInterval":25000,'
            '"pingTimeout":20000,"maxPayload":1000000}'
        )
        assert packet.kind == "open"
        assert packet.data["pingInterval"] == 25000 and packet.data["pingTimeout"] == 20000

    def test_engine_ping_and_close(self):
        assert sio.decode("2") == Packet("ping")
        assert sio.decode("1") == Packet("close")

    def test_namespace_connect(self):
        packet = sio.decode('40/exchange,{"sid":"ohpzJ6SVqtZre8MzAbAx"}')
        assert packet == Packet("connect", NS, data={"sid": "ohpzJ6SVqtZre8MzAbAx"})

    def test_root_namespace_connect(self):
        assert sio.decode("40") == Packet("connect", "/")

    def test_event_with_floats_as_decimal(self):
        packet = sio.decode(
            '42/exchange,["exchange:orderbook",{"pair":"BTCB2_BTC","bids":'
            '[{"price":0.00713454,"quantity":0.12668383,"isAmm":true}],"asks":[]}]'
        )
        assert (packet.kind, packet.namespace, packet.event) == ("event", NS, "exchange:orderbook")
        level = packet.data["bids"][0]
        assert level["price"] == Decimal("0.00713454")
        assert isinstance(level["quantity"], Decimal)

    def test_event_without_arguments(self):
        packet = sio.decode('42/exchange,["pong"]')
        assert (packet.kind, packet.event, packet.data) == ("event", "pong", None)

    def test_event_with_ack_id(self):
        packet = sio.decode('42/exchange,7["x",{"a":1}]')
        assert (packet.event, packet.data) == ("x", {"a": 1})

    def test_disconnect_and_connect_error(self):
        assert sio.decode("41/exchange,").kind == "disconnect"
        packet = sio.decode('44/exchange,{"message":"Invalid namespace"}')
        assert packet.kind == "connect_error" and packet.data == {"message": "Invalid namespace"}

    @pytest.mark.parametrize("frame", ["", "9", "4", "47/exchange,", '42/exchange,{"no":"lista"}'])
    def test_invalid_frame(self, frame):
        with pytest.raises(ProtocolError):
            sio.decode(frame)

    def test_broken_json(self):
        with pytest.raises(ProtocolError):
            sio.decode('42/exchange,["exchange:trade",{')


@pytest.mark.spec("RF-01")
class TestEncode:
    def test_connect(self):
        assert sio.connect_frame(NS) == "40/exchange,"

    def test_event(self):
        frame = sio.event_frame(NS, "subscribe", {"pair": "BTCB2_BTC"})
        assert frame == '42/exchange,["subscribe",{"pair":"BTCB2_BTC"}]'

    def test_event_without_data(self):
        assert sio.event_frame(NS, "ping") == '42/exchange,["ping"]'

    def test_round_trip(self):
        packet = sio.decode(sio.event_frame(NS, "subscribe", {"pair": "BTCB2_BTC"}))
        assert (packet.event, packet.data) == ("subscribe", {"pair": "BTCB2_BTC"})

    def test_pong(self):
        assert sio.PONG == "3"
