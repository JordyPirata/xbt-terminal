"""OrderService en dry-run (T-16, CA-04): validar, confirmar, enviar y dejarlo
todo en el journal. Book de NonKYC 0.00720000 / 0.00730000 → mid 0.00725;
saldos simulados 0.005 BTC y 0.5 BTCB2 (valor ≈ 0.008625 BTC < tope 0.01)."""

import json
from dataclasses import replace

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.core.commands import Percent, PlaceOrder
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, BookSnapshot, ConnState, D, Level, Order
from xbt_terminal.core.risk import RiskLimits
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import Failed, OrderService, Pending, Rejected, Sent

from ..fakes import T0, FakeFeed, spec

NOW = T0 + 60_000
LIMITS = RiskLimits(
    max_order_pct=D("25"),
    max_mid_deviation_pct=D("5"),
    max_venue_exposure=D("0.01"),
    stale_after_s=15,
    hanging_leg_after_s=30,
)


class Setup:
    def __init__(self, tmp_path, *, btc="0.005", btcb2="0.5", limits=LIMITS, book_ts=NOW):
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.hub_now = NOW  # hora local del hub: la de recepción del book (RNF-16)
        self.hub = MarketHub(feeds, clock=lambda: self.hub_now)
        for vid in feeds:
            self.hub.state[vid].spec = spec(vid)
        self.book("nk", "0.00720000", "0.00730000", ts=book_ts)
        balances = {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D(btcb2))}
        self.venue = DryRunVenue(feeds["nk"], balances, clock=lambda: NOW)
        self.journal_dir = tmp_path / "logs"
        self.journal = Journal(self.journal_dir, "dry-run", Redactor([]))
        ids = iter(f"t-{i:06d}" for i in range(1, 100))
        self.orders = OrderService(
            self.hub,
            {"nk": self.venue},  # nx sin keys: solo lectura
            self.journal,
            limits,
            clock=lambda: NOW,
            new_client_id=lambda: next(ids),
        )

    def book(self, venue, bid, ask, ts=NOW, seq=None, received=None):
        """`ts` es la hora del exchange; se recibe a `received` (por defecto, a `ts`)."""
        snap = BookSnapshot(venue, (Level(D(bid), D("1")),), (Level(D(ask), D("1")),), ts, seq)
        self.hub_now = ts if received is None else received
        self.hub.apply(venue, snap)
        self.hub_now = NOW

    def lines(self):
        files = sorted(self.journal_dir.glob("journal-*.jsonl"))
        return [json.loads(ln) for f in files for ln in f.read_text().splitlines()]


def buy(qty="0.01", price="0.00725000", venue="nk"):
    return PlaceOrder(venue, "buy", D(qty), D(price))


def sell(qty="0.01", price="0.00725000", venue="nk"):
    return PlaceOrder(venue, "sell", D(qty), D(price))


@pytest.mark.spec("RF-06", "RF-11", "RF-13")
class TestOrdenValida:
    async def test_prepara_con_nocional_y_fee(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())
        assert isinstance(p, Pending)
        assert p.req.client_id == "t-000001"
        assert p.notional == D("0.0000725") and p.fee == D("0.0000725") * D("0.002")
        assert "BUY 0.0100 XBT @ 0.00725000" in p.summary and "0.00007250 BTC" in p.summary
        assert "NonKYC" in p.summary
        assert s.lines() == []  # nada al journal hasta confirmar

    async def test_confirmar_envia_y_registra_sent_y_ack(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())
        result = await s.orders.confirm(p)
        assert isinstance(result, Sent) and result.order.order_id == "dry-nk-1"
        assert [o.order_id for o in await s.venue.open_orders()] == ["dry-nk-1"]
        sent, ack = s.lines()
        assert (sent["event"], ack["event"]) == ("order_sent", "order_ack")
        assert sent["client_id"] == ack["client_id"] == "t-000001"
        assert sent["mode"] == "dry-run" and sent["venue"] == "nk"
        assert sent["data"] == {"side": "buy", "qty": "0.01", "price": "0.00725000"}
        assert ack["data"] == {"order_id": "dry-nk-1", "status": "open"}

    async def test_al_confirmar_se_vuelve_a_validar(self, tmp_path):
        """El mercado puede moverse con el diálogo abierto: fallar cerrado."""
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())
        s.book("nk", "0.00800000", "0.00810000")  # el mid se va un 11 %
        result = await s.orders.confirm(p)
        assert isinstance(result, Rejected) and "mid_deviation" in result.codes
        assert await s.venue.open_orders() == []
        assert [ln["event"] for ln in s.lines()] == ["reject_local"]

    async def test_error_del_venue_queda_como_order_error(self, tmp_path):
        s = Setup(tmp_path)

        async def boom(req):
            raise VenueError("venue caído")

        s.venue.place_limit = boom
        result = await s.orders.confirm(await s.orders.prepare(buy()))
        assert isinstance(result, Failed) and "venue caído" in result.error
        assert [ln["event"] for ln in s.lines()] == ["order_sent", "order_error"]
        assert s.lines()[1]["data"] == {"error": "venue caído"}


def codes(result):
    assert isinstance(result, Rejected), result
    return set(result.codes)


@pytest.mark.spec("CA-04", "RF-10", "RF-11")
class TestRechazos:
    """Cada regla de RF-10 rechaza en dry-run, queda `reject_local` y no se envía nada."""

    async def check(self, s, cmd, code):
        result = await s.orders.prepare(cmd)
        assert code in codes(result)
        last = s.lines()[-1]
        assert last["event"] == "reject_local" and last["venue"] == cmd.venue
        assert code in {v["code"] for v in last["data"]["violations"]}
        assert last["data"]["price"] == str(cmd.price)
        return result

    async def test_order_size(self, tmp_path):
        """Inventario 0.005 + 0.5 × 0.00725 = 0.008625 BTC; 25 % = 0.00215625."""
        s = Setup(tmp_path)
        assert isinstance(await s.orders.prepare(buy(qty="0.2974")), Pending)  # 0.00215615
        await self.check(s, buy(qty="0.2975"), "order_size")  # 0.00215688

    async def test_order_size_suma_los_dos_venues(self, tmp_path):
        """Un venue sin keys no cuenta; con los dos, el tope sube."""
        from xbt_terminal.adapters.dryrun import DryRunVenue

        s = Setup(tmp_path)
        s.book("nx", "0.00720000", "0.00730000")
        bal = {"BTC": Balance("BTC", D("0.005")), "BTCB2": Balance("BTCB2", D("0.5"))}
        s.orders._venues["nx"] = DryRunVenue(s.hub._venues["nx"], bal, clock=lambda: NOW)
        assert isinstance(await s.orders.prepare(buy(qty="0.5")), Pending)  # 0.003625 < 0.0043125

    async def test_order_size_sin_mid_del_otro_venue(self, tmp_path):
        """Sin mid de un venue no hay inventario total: rechazo (fallar cerrado)."""
        from xbt_terminal.adapters.dryrun import DryRunVenue

        s = Setup(tmp_path)
        bal = {"BTC": Balance("BTC", D("0.005"))}
        s.orders._venues["nx"] = DryRunVenue(s.hub._venues["nx"], bal, clock=lambda: NOW)
        await self.check(s, buy(), "order_size")

    async def test_mid_deviation(self, tmp_path):
        await self.check(Setup(tmp_path), buy(price="0.00800000"), "mid_deviation")

    async def test_venue_exposure(self, tmp_path):
        s = Setup(tmp_path, limits=replace(LIMITS, max_venue_exposure=D("0.008")))
        await self.check(s, buy(), "venue_exposure")

    async def test_self_cross(self, tmp_path):
        s = Setup(tmp_path)
        assert isinstance(
            await s.orders.confirm(await s.orders.prepare(sell(price="0.00726000"))), Sent
        )
        await self.check(s, buy(price="0.00727000"), "self_cross")

    async def test_tick(self, tmp_path):
        await self.check(Setup(tmp_path), buy(price="0.007250001"), "tick")

    async def test_lot(self, tmp_path):
        await self.check(Setup(tmp_path), buy(qty="0.01005"), "lot")

    async def test_min_qty(self, tmp_path):
        await self.check(Setup(tmp_path), buy(qty="0.00005"), "min_qty")

    async def test_min_notional(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].spec = replace(spec("nk"), min_notional=D("0.001"))
        await self.check(s, buy(), "min_notional")

    async def test_stale_sin_book(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].book.valid = False
        await self.check(s, buy(), "stale_data")

    async def test_insufficient_balance(self, tmp_path):
        await self.check(Setup(tmp_path, btc="0.00001"), buy(), "insufficient_balance")

    async def test_venue_sin_keys(self, tmp_path):
        result = await self.check(Setup(tmp_path), buy(venue="nx"), "read_only")
        assert "sin keys" in result.message

    async def test_mercado_sin_cargar(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].spec = None
        await self.check(s, buy(), "stale_data")

    async def test_mensaje_con_importes_en_btc(self, tmp_path):
        """Los :.2f heredados de USDT mostraban 0.00 BTC (D-13)."""
        s = Setup(tmp_path, limits=replace(LIMITS, max_order_pct=D("1")))
        result = await s.orders.prepare(buy(qty="0.02"))
        assert "0.00014500" in result.message and "0.00 " not in result.message


@pytest.mark.spec("RF-10", "CA-04", "RNF-16", "RNF-24", "CA-18")
class TestFrescura:
    """Un book quieto solo vale si lo confirma el contraste con el REST (D-76);
    la conexión viva ya no basta (D-13 corregida)."""

    async def test_book_quieto_confirmado_por_el_rest_vale(self, tmp_path):
        s = Setup(tmp_path, book_ts=NOW - 300_000)  # 5 min sin cambios, como NonKYC
        s.hub.state["nk"].checked_at = NOW - 4_000
        assert isinstance(await s.orders.prepare(buy()), Pending)

    async def test_book_quieto_con_latido_reciente_ya_no_vale(self, tmp_path):
        """Así disparó d-9820a4: WS conectado, sin cambios del book desde hacía 2 h."""
        s = Setup(tmp_path, book_ts=NOW - 300_000)
        s.hub_now = NOW
        s.hub.apply("nk", ConnState("nk", "market", "ws", "up", 150.0, NOW))
        assert "stale_data" in codes(await s.orders.prepare(buy()))

    async def test_contraste_viejo_no_vale(self, tmp_path):
        """Con el REST fallando no hay confirmaciones: a los stale_after_s, viejo."""
        s = Setup(tmp_path, book_ts=NOW - 300_000)
        s.hub.state["nk"].checked_at = NOW - 16_000
        assert "stale_data" in codes(await s.orders.prepare(buy()))

    async def test_book_en_duda_no_vale_aunque_sea_reciente(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].checked_at = NOW
        s.hub.state["nk"].suspect = True  # el último contraste no coincidió
        assert "stale_data" in codes(await s.orders.prepare(buy()))

    async def test_book_quieto_sin_estado_no_vale(self, tmp_path):
        s = Setup(tmp_path, book_ts=NOW - 300_000)
        assert "stale_data" in codes(await s.orders.prepare(buy()))


@pytest.mark.spec("RF-10", "RF-13")
class TestCuentaIlegible:
    """D-16: si no se pueden leer saldos u órdenes al validar, se rechaza (no se lanza)."""

    @pytest.mark.parametrize(
        "exc", [VenueError("NonKYC: HTTP 429: Too many requests"), httpx.ConnectError("sin red")]
    )
    async def test_saldos_ilegibles(self, tmp_path, exc):
        s = Setup(tmp_path)

        async def boom():
            raise exc

        s.venue.balances = boom
        result = await s.orders.prepare(buy())
        assert "venue_error" in codes(result)
        assert "no se pudieron leer" in result.message
        assert s.lines()[-1]["event"] == "reject_local"

    async def test_ordenes_ilegibles_al_confirmar(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())

        async def boom():
            raise httpx.ReadTimeout("lento")

        s.venue.open_orders = boom
        result = await s.orders.confirm(p)
        assert isinstance(result, Rejected) and "venue_error" in result.codes
        assert [ln["event"] for ln in s.lines()] == ["reject_local"]


class Counting:
    """Cuenta las lecturas al venue que hace OrderService."""

    def __init__(self, venue):
        self.calls = []
        for name in ("balances", "open_orders"):
            real = getattr(venue, name)

            async def wrapped(_real=real, _name=name, **kw):
                self.calls.append(_name)
                return await _real(**kw)

            setattr(venue, name, wrapped)


def cached(tmp_path, *, age_ms=0, btc="0.005", orders=()):
    """Setup con AccountService ya cargado (como tras su primer refresco)."""
    from xbt_terminal.services.account import AccountService

    s = Setup(tmp_path)
    account = AccountService({"nk": s.venue}, clock=lambda: NOW)
    st = account.state["nk"]
    st.balances = {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D("0.5"))}
    st.balances_at = NOW - age_ms
    st.orders = {o.order_id: o for o in orders}
    ids = iter(f"t-{i:06d}" for i in range(1, 100))
    s.orders = OrderService(
        s.hub, {"nk": s.venue}, s.journal, LIMITS, clock=lambda: NOW,
        new_client_id=lambda: next(ids), account=account,
    )  # fmt: skip
    s.account = account
    s.counter = Counting(s.venue)
    return s


@pytest.mark.spec("RNF-11", "RF-10")
class TestValidacionConCache:
    """D-18: con la cuenta al día no se pregunta al venue al preparar ni al confirmar."""

    async def test_cache_fresca_sin_peticiones(self, tmp_path):
        s = cached(tmp_path, age_ms=29_000)
        p = await s.orders.prepare(buy())
        assert isinstance(p, Pending)
        await s.orders._validate(p.req)  # lo que hace confirm antes de enviar
        assert s.counter.calls == []

    async def test_la_cache_manda_en_las_reglas(self, tmp_path):
        s = cached(tmp_path, btc="0.00001")  # el venue tiene 0.005; la cuenta, casi nada
        assert "insufficient_balance" in codes(await s.orders.prepare(buy()))

    async def test_cache_vieja_pregunta_al_venue(self, tmp_path):
        s = cached(tmp_path, age_ms=30_001)
        assert isinstance(await s.orders.prepare(buy()), Pending)
        assert sorted(s.counter.calls) == ["balances", "open_orders"]

    async def test_sin_saldos_cargados_pregunta_al_venue(self, tmp_path):
        s = cached(tmp_path)
        s.account.state["nk"].balances = None
        await s.orders.prepare(buy())
        assert "balances" in s.counter.calls

    async def test_las_ordenes_de_la_cuenta_cuentan(self, tmp_path):
        mine = Order("nk", "x-1", None, "sell", D("0.00726000"), D("0.01"), D("0"), "open", NOW)
        s = cached(tmp_path, orders=[mine])
        assert "self_cross" in codes(await s.orders.prepare(buy(price="0.00727000")))

    async def test_una_orden_recien_enviada_cuenta_aunque_la_cuenta_no_la_vea(self, tmp_path):
        s = cached(tmp_path)
        sent = await s.orders.confirm(await s.orders.prepare(sell(price="0.00726000")))
        assert isinstance(sent, Sent) and s.account.state["nk"].orders == {}
        assert "self_cross" in codes(await s.orders.prepare(buy(price="0.00727000")))

    async def test_cuando_la_cuenta_la_ve_terminada_deja_de_contar(self, tmp_path):
        s = cached(tmp_path)
        sent = await s.orders.confirm(await s.orders.prepare(sell(price="0.00726000")))
        s.orders.on_private_event("nk", replace(sent.order, status="cancelled"))
        assert isinstance(await s.orders.prepare(buy(price="0.00727000")), Pending)


@pytest.mark.spec("RF-10")
async def test_saldo_insuficiente_con_8_decimales(tmp_path):
    """nocional × (1 + fee) sale con muchos decimales; el mensaje los redondea a 8."""
    s = Setup(tmp_path, btc="0.00001")
    result = await s.orders.prepare(buy(qty="0.0137", price="0.00725001"))
    msg = next(v.message for v in result.violations if v.code == "insufficient_balance")
    assert msg == "hace falta 0.00009952 BTC, disponible 0.00001000"


def pct(side, value, price="0.00725000", venue="nk"):
    return PlaceOrder(venue, side, Percent(D(value)), D(price))


@pytest.mark.spec("RF-17", "RF-10")
class TestCantidadPorcentual:
    """D-21. Saldos 0.005 BTC y 0.5 BTCB2; inventario 0.008625 BTC, tope 0.00215625."""

    async def test_compra_con_el_25_del_btc(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(pct("buy", "25"))
        assert isinstance(p, Pending)
        assert p.req.qty == D("0.1720")  # 0.00125 / (0.00725 × 1.002) = 0.17207…
        assert "BUY 0.1720 XBT" in p.summary
        assert "25 % de 0.00500000 BTC en NonKYC" in p.summary

    async def test_venta_con_el_20_del_btcb2(self, tmp_path):
        p = await Setup(tmp_path).orders.prepare(pct("sell", "20"))
        assert isinstance(p, Pending) and p.req.qty == D("0.1")
        assert "20 % de 0.50000000 XBT en NonKYC" in p.summary

    async def test_confirmar_envia_la_cantidad_fijada(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(pct("buy", "25"))
        assert isinstance(await s.orders.confirm(p), Sent)
        assert s.lines()[0]["data"]["qty"] == "0.1720"

    async def test_las_reglas_se_aplican_igual(self, tmp_path):
        s = Setup(tmp_path)
        result = await s.orders.prepare(pct("buy", "50"))  # 0.0025 BTC > tope 0.00215625
        assert "order_size" in codes(result)
        last = s.lines()[-1]
        assert last["event"] == "reject_local" and last["data"]["qty"] == "0.3441"

    async def test_sin_saldo_se_rechaza(self, tmp_path):
        s = Setup(tmp_path, btcb2="0")
        result = await s.orders.prepare(pct("sell", "100"))
        assert codes(result) == {"insufficient_balance"}
        assert "100 % de 0.00000000 XBT" in result.message and "lote" in result.message
        assert s.lines()[-1]["event"] == "reject_local"

    async def test_lote_fino_sin_notacion_cientifica(self, tmp_path):
        s = Setup(tmp_path, btcb2="0")
        s.hub.state["nk"].spec = replace(spec("nk"), lot_size=D("1E-8"), min_qty=D("1E-8"))
        result = await s.orders.prepare(pct("sell", "100"))
        assert "un lote (0.00000001 XBT)" in result.message

    async def test_venue_sin_keys(self, tmp_path):
        assert "read_only" in codes(
            await Setup(tmp_path).orders.prepare(pct("buy", "25", venue="nx"))
        )

    async def test_mercado_sin_cargar(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].spec = None
        assert "stale_data" in codes(await s.orders.prepare(pct("buy", "25")))

    async def test_saldos_ilegibles(self, tmp_path):
        s = Setup(tmp_path)

        async def boom():
            raise httpx.ConnectError("sin red")

        s.venue.balances = boom
        assert "venue_error" in codes(await s.orders.prepare(pct("buy", "25")))

    async def test_usa_los_saldos_de_la_cuenta(self, tmp_path):
        s = cached(tmp_path, btc="0.002")  # el venue tiene 0.005; la cuenta, 0.002
        p = await s.orders.prepare(pct("buy", "25"))
        assert isinstance(p, Pending) and p.req.qty == D("0.0688")
        assert s.counter.calls == []
