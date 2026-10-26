"""Orden doble en OrderService (T-18, design.md §6.4) con dos DryRunVenue.
Books: NonKYC 0.00720000 / 0.00730000, Neoxa 0.00722000 / 0.00732000."""

import json

import pytest

from btcb2_terminal.adapters.base import VenueError
from btcb2_terminal.adapters.dryrun import DryRunVenue
from btcb2_terminal.core.commands import DoubleOrder
from btcb2_terminal.core.journal import Journal
from btcb2_terminal.core.models import Balance, BookSnapshot, D, Level
from btcb2_terminal.core.secrets import Redactor
from btcb2_terminal.services.market import MarketHub
from btcb2_terminal.services.orders import (
    DoubleRejected,
    DoubleSent,
    OrderService,
    PendingDouble,
)

from ..fakes import T0, FakeFeed, spec
from .test_orders import LIMITS

NOW = T0 + 60_000


class Setup:
    def __init__(self, tmp_path, nk_btc="0.005", nx_btcb2="0.5"):
        self.now = NOW
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.hub = MarketHub(feeds, clock=lambda: self.now)
        for vid in feeds:
            self.hub.state[vid].spec = spec(vid)
        self.book("nk", "0.00720000", "0.00730000")
        self.book("nx", "0.00722000", "0.00732000")
        self.venues = {
            "nk": DryRunVenue(feeds["nk"], self.balances(nk_btc, "0"), clock=lambda: self.now),
            "nx": DryRunVenue(feeds["nx"], self.balances("0", nx_btcb2), clock=lambda: self.now),
        }
        self.logs = tmp_path / "logs"
        self.journal = Journal(self.logs, "dry-run", Redactor([]))
        ids = iter(f"t-{i:06d}" for i in range(1, 100))
        self.orders = OrderService(
            self.hub,
            self.venues,
            self.journal,
            LIMITS,
            clock=lambda: self.now,
            new_client_id=lambda: next(ids),
            new_double_id=lambda: "d-000001",
        )

    @staticmethod
    def balances(btc, btcb2):
        return {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D(btcb2))}

    def book(self, venue, bid, ask):
        snap = BookSnapshot(venue, (Level(D(bid), D("1")),), (Level(D(ask), D("1")),), self.now)
        self.hub.apply(venue, snap)

    def lines(self):
        files = sorted(self.logs.glob("journal-*.jsonl"))
        return [json.loads(ln) for f in files for ln in f.read_text().splitlines()]

    def events(self):
        return [(ln["event"], ln["venue"]) for ln in self.lines()]

    async def feed(self, venue):
        """Pasa a OrderService lo que el venue simulado ha publicado (como AccountService)."""
        for order in await self.venues[venue].open_orders():
            self.orders.on_private_event(venue, order)


def dbl(qty="0.01", buy="0.00725000", sell="0.00726000"):
    return DoubleOrder(D(qty), "nk", D(buy), "nx", D(sell))


@pytest.mark.spec("RF-07", "RF-10")
class TestPreparar:
    async def test_dos_piernas_y_un_resumen(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare_double(dbl())
        assert isinstance(p, PendingDouble) and p.id == "d-000001"
        assert (p.buy.req.venue, p.buy.req.side, p.sell.req.venue, p.sell.req.side) == (
            "nk", "buy", "nx", "sell",
        )  # fmt: skip
        assert p.gross == D("0.0000001")  # (0.00726 - 0.00725) × 0.01
        assert p.net == D("0.0000726") * D("0.998") - D("0.0000725") * D("1.002")
        text = p.summary
        assert "COMPRA 0.0100 XBT @ 0.00725000 en NonKYC" in text
        assert "VENTA 0.01000000 XBT @ 0.00726000 en Neoxa" in text
        assert "bruto +0.00000010 BTC (+10 sats)" in text and "neto -0.00000019 BTC" in text
        assert s.lines() == []

    async def test_una_pierna_rechazada_no_envia_ninguna(self, tmp_path):
        s = Setup(tmp_path, nx_btcb2="0")  # Neoxa sin BTCB2 para vender
        r = await s.orders.prepare_double(dbl())
        assert isinstance(r, DoubleRejected)
        assert "no se envía ninguna pierna" in r.message and "venta en Neoxa" in r.message
        assert "insufficient_balance" in r.codes
        assert s.events() == [("reject_local", "nx")]
        assert s.lines()[0]["data"]["double"] == "d-000001"
        for venue in s.venues.values():
            assert await venue.open_orders() == []

    async def test_las_dos_rechazadas(self, tmp_path):
        s = Setup(tmp_path, nk_btc="0", nx_btcb2="0")
        r = await s.orders.prepare_double(dbl())
        assert isinstance(r, DoubleRejected)
        assert s.events() == [("reject_local", "nk"), ("reject_local", "nx")]


@pytest.mark.spec("RF-07", "CA-03")
class TestEnviar:
    async def test_envia_las_dos_y_las_sigue(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        assert isinstance(sent, DoubleSent)
        st = sent.state
        assert (st.buy.status, st.sell.status) == ("open", "open")
        assert (st.buy.order_id, st.sell.order_id) == ("dry-nk-1", "dry-nx-1")
        assert sorted(s.events()) == sorted(
            [("order_sent", "nk"), ("order_sent", "nx"), ("order_ack", "nk"), ("order_ack", "nx")]
        )
        assert {ln["data"]["double"] for ln in s.lines()} == {"d-000001"}
        assert s.orders.doubles["d-000001"] == st

    async def test_al_confirmar_se_revalida_y_si_falla_no_sale_ninguna(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare_double(dbl())
        s.book("nx", "0.00800000", "0.00810000")  # el mid de Neoxa se va un 10 %
        r = await s.orders.confirm_double(p)
        assert isinstance(r, DoubleRejected) and "mid_deviation" in r.codes
        assert s.events() == [("reject_local", "nx")]
        assert "d-000001" not in s.orders.doubles

    async def test_error_al_enviar_una_pierna(self, tmp_path):
        s = Setup(tmp_path)

        async def boom(req):
            raise VenueError("Neoxa: HTTP 400: PRICE_OUT_OF_BAND")

        s.venues["nx"].place_limit = boom
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        st = sent.state
        assert (st.buy.status, st.sell.status) == ("open", "rejected")
        assert "PRICE_OUT_OF_BAND" in (st.sell.error or "")
        assert ("order_error", "nx") in s.events() and ("order_ack", "nk") in s.events()

    async def test_los_cambios_de_orden_actualizan_la_pierna(self, tmp_path):
        s = Setup(tmp_path)
        seen = []
        s.orders.subscribe_doubles(seen.append)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.004"))
        await s.feed("nk")
        st = s.orders.doubles["d-000001"]
        assert (st.buy.status, st.buy.filled) == ("partial", D("0.004"))
        assert "d-000001" in seen


@pytest.mark.spec("RF-07", "CA-03")
class TestPiernaColgada:
    async def test_llena_y_la_otra_abierta_mas_de_n_segundos(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        order = await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        s.orders.on_private_event("nk", order)
        s.now += LIMITS.hanging_leg_after_s * 1000
        assert s.orders.check_doubles() == []  # el límite exacto aún no
        s.now += 1
        assert s.orders.check_doubles() == ["d-000001"]
        (line,) = [ln for ln in s.lines() if ln["event"] == "hanging_leg"]
        assert line["venue"] is None and line["data"]["double"] == "d-000001"
        assert "venta en Neoxa" in line["data"]["reason"]
        assert line["data"]["legs"]["buy"]["filled"] == "0.01"
        assert s.orders.hanging["d-000001"].startswith("venta en Neoxa")

    async def test_se_avisa_una_sola_vez(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        s.orders.on_private_event(
            "nk", await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        )
        s.now += 31_000
        s.orders.check_doubles()
        s.now += 10_000
        assert s.orders.check_doubles() == []
        assert [ln["event"] for ln in s.lines()].count("hanging_leg") == 1

    async def test_rechazada_al_enviar_y_la_otra_llena_es_inmediato(self, tmp_path):
        s = Setup(tmp_path)

        async def boom(req):
            raise VenueError("rechazada por el venue")

        s.venues["nx"].place_limit = boom
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        s.orders.on_private_event(
            "nk", await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        )
        assert s.orders.check_doubles() == ["d-000001"]

    async def test_las_dos_llenas_no_es_colgada(self, tmp_path):
        s = Setup(tmp_path)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(dbl()))
        for venue, leg in (("nk", sent.state.buy), ("nx", sent.state.sell)):
            s.orders.on_private_event(
                venue, await s.venues[venue].simulate_fill(leg.order_id, D("0.01"))
            )
        s.now += 120_000
        assert s.orders.check_doubles() == [] and s.orders.doubles["d-000001"].done
