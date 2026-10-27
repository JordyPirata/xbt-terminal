"""BotService en dry-run (T-36, RF-19 a RF-21, RF-23, CA-08, CA-09): hub con venues
falsos, DryRunVenue con saldos fijos, cuenta, órdenes y bot, todos de verdad."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from xbt_terminal.adapters.base import UncertainError, VenueError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.config import BotConfig
from xbt_terminal.core.bot import DAY_MS, BotLimits, FeeToken
from xbt_terminal.core.double import start
from xbt_terminal.core.inventory import InventoryTarget
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, BookSnapshot, ConnState, D, Level, OrderRequest
from xbt_terminal.core.risk import RiskLimits
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.account import AccountService
from xbt_terminal.services.bot import BotService
from xbt_terminal.services.faults import Faults
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import OrderService

from ..fakes import T0, FakeFeed

NOW = T0 + 60_000
RISK = RiskLimits(
    max_order_pct=D("100"),
    max_mid_deviation_pct=D("5"),
    max_venue_exposure=D("100"),
    stale_after_s=15,
    hanging_leg_after_s=30,
    max_clock_skew_s=5,
)
BOT = BotLimits(
    min_net_pct=D("0.5"),
    max_slippage_pct=D("0.2"),
    max_qty=D("10"),
    fill_timeout_ms=100,
    cooldown_s=60,
    max_trades_per_hour=20,
    max_daily_btc=D("1"),
    max_consecutive_failures=3,
)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class BotRig:
    def __init__(
        self, tmp_path, *, live=False, enabled=True, limits=BOT, risk=RISK, wire=True,
        fee_tokens=None, nk_extra=None,
    ):  # fmt: skip
        self.clock = Clock()
        self.feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.faults = Faults()
        self.hub = MarketHub(self.feeds, history=50, retry_s=0.01, clock=self.clock)
        bal = {"BTC": Balance("BTC", D("1")), "BTCB2": Balance("BTCB2", D("1000"))}
        own = {"nk": bal | (nk_extra or {}), "nx": bal}
        self.venues = {
            vid: DryRunVenue(feed, own[vid], spec=lambda vid=vid: self.hub.state[vid].spec,
                             clock=self.clock)
            for vid, feed in self.feeds.items()
        }  # fmt: skip
        self.logs = tmp_path / "logs"
        self.journal = Journal(self.logs, "live" if live else "dry-run", Redactor([]))
        self.account = AccountService(
            self.venues, journal=self.journal, retry_s=0.01, clock=self.clock, faults=self.faults
        )
        self.orders = OrderService(
            self.hub, self.venues, self.journal, risk, account=self.account,
            faults=self.faults, clock=self.clock, live=live,
        )  # fmt: skip
        if wire:  # sin `wire`, el enlace lo tiene que hacer el Engine (D-61)
            self.account.subscribe_events(self.orders.on_private_event)
        self.fee_tokens = fee_tokens or {}
        self.bot = self.new_bot(live=live, enabled=enabled, limits=limits)
        self.tasks: list[asyncio.Task] = []

    def new_bot(self, *, live=False, enabled=True, limits=BOT):
        return BotService(
            self.hub, self.orders, self.account, self.journal, BotConfig(enabled, limits),
            live=live, faults=self.faults, clock=self.clock, settle_s=1.0, tick_s=0.02,
            fee_tokens=self.fee_tokens,
        )  # fmt: skip

    async def __aenter__(self):
        for work in (self.hub.run(), self.account.run(), self.bot.run()):
            self.tasks.append(asyncio.create_task(work))
        await self.until(lambda: all(s.ready for s in self.hub.state.values()))
        await self.until(lambda: all(s.balances for s in self.account.state.values()))
        return self

    async def __aexit__(self, *exc):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.journal.close()

    async def until(self, predicate, timeout=2.0):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.005)

    def books(self, nk=("0.00690000", "0.00700000"), nx=("0.00720000", "0.00730000")):
        """Por defecto cruzan: comprar en NonKYC a 0.007 y vender en Neoxa a 0.0072."""
        for vid, (bid, ask) in (("nk", nk), ("nx", nx)):
            snap = BookSnapshot(
                vid, (Level(D(bid), D("50")),), (Level(D(ask), D("50")),), self.clock.now
            )
            self.feeds[vid].push(ConnState(vid, "market", "ws", "up", 10.0, self.clock.now), snap)

    def events(self, name=None):
        rows = [
            json.loads(line)
            for f in sorted(self.logs.glob("journal-*.jsonl"))
            for line in f.read_text().splitlines()
        ]
        return [r for r in rows if name is None or r["event"] == name]

    async def open_orders(self):
        return {vid: await v.open_orders() for vid, v in self.venues.items()}


@pytest.fixture
async def rig(tmp_path):
    async with BotRig(tmp_path) as r:
        yield r


@pytest.mark.spec("RF-19", "RF-20", "RF-23", "CA-08")
class TestDisparo:
    async def test_un_cruce_una_sola_doble(self, rig):
        assert rig.bot.arm() is None
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        rig.books()  # el cruce sigue, pero está enfriando
        await asyncio.sleep(0.1)
        assert len(rig.orders.doubles) == 1
        (double,) = rig.orders.doubles.values()
        assert (double.buy.req.venue, double.buy.req.price) == ("nk", D("0.00701400"))
        assert (double.sell.req.venue, double.sell.req.price) == ("nx", D("0.00718560"))
        assert double.buy.req.qty == D("10")  # max_qty
        # el dry-run no llena: a los fill_timeout_ms se cancelan las dos
        assert {leg.status for leg in double.legs} == {"cancelled"}
        assert await rig.open_orders() == {"nk": [], "nx": []}
        (fire,) = rig.events("bot_fire")
        assert fire["data"]["double"] == double.id and D(fire["data"]["qty"]) == 10
        assert fire["data"]["buy_venue"] == "nk" and fire["data"]["sell_venue"] == "nx"
        states = [e["data"]["status"] for e in rig.events("bot_state")]
        assert states == ["armed", "firing", "cooldown"]
        assert len(rig.events("order_sent")) == 2 and len(rig.events("cancel_sent")) == 2

    async def test_tras_el_enfriamiento_vuelve_a_disparar(self, rig):
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        rig.clock.now += 60_000
        rig.books()
        await rig.until(lambda: len(rig.orders.doubles) == 2)

    async def test_las_dos_llenas_enfria(self, rig):
        rig.bot.arm()
        rig.bot._limits = replace(BOT, fill_timeout_ms=2_000)
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (double,) = rig.orders.doubles.values()
        await rig.until(lambda: all(leg.order_id for leg in double.legs))
        for leg in double.legs:
            await rig.venues[leg.req.venue].simulate_fill(leg.order_id, D("10"))
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        assert rig.events("cancel_sent") == []

    async def test_sin_cruce_no_dispara(self, rig):
        rig.bot.arm()
        rig.books(nx=("0.00700500", "0.00730000"))  # neta < 0.5 %
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and rig.bot.state.status == "armed"
        assert rig.bot.last_skip.reason == "below_threshold"

    async def test_apagado_no_dispara(self, rig):
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and rig.bot.state.status == "off"


@pytest.mark.spec("RF-19", "CA-08")
class TestBloqueos:
    async def test_book_viejo(self, rig):
        rig.books()
        await rig.until(lambda: rig.hub.state["nx"].book.valid)
        rig.clock.now += 16_000  # más que stale_after_s
        rig.bot.arm()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {}
        assert rig.bot.last_skip.reason == "blocked" and "book viejo" in rig.bot.last_skip.detail

    async def test_fallo_del_venue(self, rig):
        rig.hub.state["nx"].error = "RuntimeError: bug"
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and "nx" in rig.bot.last_skip.detail

    async def test_cuenta_con_error_sin_texto(self, rig):
        """D-51: el 2026-10-23 el bot disparó con los saldos fallando porque el
        error de la cuenta era "" (ConnectTimeout sin texto)."""
        rig.bot.arm()
        rig.account.state["nx"].error = ""
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {}
        assert rig.bot.last_skip.reason == "blocked" and "nx" in rig.bot.last_skip.detail

    async def test_mercado_con_error_sin_texto(self, rig):
        rig.hub.state["nk"].error = ""
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and "nk" in rig.bot.last_skip.detail

    async def test_orden_incierta_abierta(self, rig):
        rig.orders.uncertain["t-x"] = object()  # cualquier incierta bloquea
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and rig.bot.last_skip.reason == "uncertain_open"

    async def test_otra_doble_sin_terminar(self, rig):
        """Una doble manual abierta: el bot no manda otra encima (una a la vez)."""
        buy = OrderRequest("nk", "buy", D("1"), D("0.007"), "c-1")
        sell = OrderRequest("nx", "sell", D("1"), D("0.0072"), "c-2")
        rig.orders.doubles["d-manual"] = start("d-manual", buy, sell, NOW)
        rig.bot.arm()
        rig.books()
        await asyncio.sleep(0.1)
        assert set(rig.orders.doubles) == {"d-manual"}
        assert rig.bot.last_skip.reason == "double_open"


@pytest.mark.spec("RF-19")
async def test_en_live_sin_desfase_medido_no_dispara(tmp_path):
    async with BotRig(tmp_path, live=True) as rig:
        assert rig.bot.arm() is None
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.orders.doubles == {} and "reloj" in rig.bot.last_skip.detail


@pytest.mark.spec("RF-18")
async def test_en_live_hace_falta_enabled(tmp_path):
    async with BotRig(tmp_path, live=True, enabled=False) as rig:
        assert "enabled" in rig.bot.arm()
        assert rig.bot.state.status == "off"
    async with BotRig(tmp_path / "dry", enabled=False) as rig:
        assert rig.bot.arm() is None  # en dry-run, siempre


@pytest.mark.spec("RF-18", "RF-20")
async def test_la_confirmacion_muestra_el_recorrido_del_book(tmp_path):
    async with BotRig(tmp_path) as rig:
        assert "recorre" not in rig.bot.summary
    walk = replace(BOT, max_walk_pct={"nk": D("0.8"), "nx": D("0")})
    async with BotRig(tmp_path / "walk", limits=walk) as rig:
        assert "recorre el book hasta NonKYC 0.8 % del mejor precio" in rig.bot.summary
        assert "Neoxa" not in rig.bot.summary.split("recorre")[1]


@pytest.mark.spec("RF-21", "CA-09")
class TestParada:
    async def test_pierna_rechazada_por_el_venue(self, rig):
        async def refuse(req):
            raise VenueError("Neoxa: [400] rechazada")

        rig.venues["nx"].place_limit = refuse
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("rejected")
        assert await rig.open_orders() == {"nk": [], "nx": []}  # la compra, cancelada
        (halt,) = [e for e in rig.events("bot_state") if e["data"]["status"] == "halted"]
        assert "rechazada" in halt["data"]["reason"]

    async def test_pierna_sin_conexion_dice_no_enviada(self, rig):
        """D-51: sin conectar, la orden no salió; el paro no debe decir "rechazada"."""

        async def offline(req):
            raise httpx.ConnectTimeout("")

        rig.venues["nx"].place_limit = offline
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert "no enviada, sin conexión (ConnectTimeout)" in rig.bot.state.reason
        (err,) = rig.events("order_error")
        assert err["venue"] == "nx" and err["data"]["error"] == (
            "no enviada, sin conexión (ConnectTimeout)"
        )

    async def test_pierna_incierta(self, rig):
        async def unclear(req):
            raise UncertainError("Neoxa: sin respuesta clara (ReadTimeout)")

        rig.venues["nx"].place_limit = unclear
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("uncertain")
        assert (await rig.open_orders())["nk"] == []

    async def test_llenado_desigual_es_pierna_colgada(self, rig):
        rig.bot.arm()
        rig.bot._limits = replace(BOT, fill_timeout_ms=300)
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (double,) = rig.orders.doubles.values()
        await rig.until(lambda: double.buy.order_id is not None)
        await rig.venues["nk"].simulate_fill(double.buy.order_id, D("4"))
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("hanging_leg")
        assert await rig.open_orders() == {"nk": [], "nx": []}
        rig.clock.now += 3_600_000
        rig.books()
        await asyncio.sleep(0.1)
        assert len(rig.orders.doubles) == 1  # parado hasta que se rearme a mano

    async def test_rechazos_de_riesgo_seguidos(self, tmp_path):
        risk = replace(RISK, max_venue_exposure=D("0.0001"))
        async with BotRig(tmp_path, risk=risk, limits=replace(BOT, cooldown_s=2)) as rig:
            rig.bot.arm()
            for n in range(1, 4):
                rig.books()
                await rig.until(lambda n=n: len(rig.events("bot_fire")) == n)
                rig.clock.now += 2_000
            await rig.until(lambda: rig.bot.state.status == "halted")
            assert "3 disparos rechazados" in rig.bot.state.reason
            assert rig.events("order_sent") == []

    async def test_un_bug_para_el_bot(self, rig):
        def broken(now):
            raise RuntimeError("bug en el bot")

        rig.bot._views = broken
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert "bug en el bot" in rig.bot.state.reason
        assert rig.faults.count == 1 and rig.faults.recent[0].where == "bot"
        del rig.bot._views
        await asyncio.sleep(0.1)
        assert rig.bot.state.status == "halted"  # no se rearma solo


@pytest.mark.spec("RF-22", "RF-23")
async def test_los_contadores_se_rehacen_del_journal(tmp_path):
    async with BotRig(tmp_path) as rig:
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        first = rig.bot.state
        again = rig.new_bot()  # como al reiniciar la terminal
        assert again.state.status == "off"
        assert again.state.fired_at == first.fired_at
        assert again.state.day == NOW // DAY_MS and again.state.day_btc == first.day_btc
        assert again.state.last_fire_at == first.last_fire_at


@pytest.mark.spec("RF-22")
class TestTopeDiario:
    """T-46 (D-54): el tope diario para el bot también cuando no lo agota un disparo."""

    async def test_queda_menos_de_un_lote(self, tmp_path):
        # el primer disparo gasta 10 × 0.007014 = 0.07014 BTC y deja 0.0000005,
        # menos que un lote de 0.0001 a ese precio
        limits = replace(BOT, max_daily_btc=D("0.0701405"))
        async with BotRig(tmp_path, limits=limits) as rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            rig.clock.now += 60_000
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "halted")
            assert rig.bot.state.reason.startswith("day_cap")
            assert len(rig.orders.doubles) == 1
            assert rig.events("bot_state")[-1]["data"]["status"] == "halted"

    async def test_rearmar_el_mismo_dia_con_el_tope_gastado(self, rig):
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "cooldown")
        rig.bot.disarm()
        rig.bot._limits = replace(BOT, max_daily_btc=D("0.05"))  # ya se gastaron 0.07014
        rig.clock.now += 60_000
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("day_cap")
        assert "day_cap" in rig.bot.status_text()
        assert len(rig.orders.doubles) == 1

    async def test_tras_reiniciar_con_el_tope_gastado(self, tmp_path):
        async with BotRig(tmp_path) as rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            rig.bot.disarm()
            rig.clock.now += 60_000
            again = rig.new_bot(limits=replace(BOT, max_daily_btc=D("0.05")))  # reinicio
            rig.tasks.append(asyncio.create_task(again.run()))
            assert again.arm() is None
            rig.books()
            await rig.until(lambda: again.state.status == "halted")
            assert again.state.reason.startswith("day_cap")
            assert len(rig.orders.doubles) == 1


@pytest.mark.spec("RF-18", "RF-22")
async def test_apagar_mientras_dispara_lo_deja_apagado(tmp_path):
    """Visto en real (2026-10-22 03:34): bot off con una doble en vuelo, y al
    cerrarla el bot pasaba a cooldown y luego se rearmaba solo."""
    async with BotRig(tmp_path, limits=replace(BOT, cooldown_s=2)) as rig:
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "firing")
        rig.bot.disarm()
        await rig.until(lambda: rig.events("cancel_ack") and len(rig.events("cancel_ack")) == 2)
        await asyncio.sleep(0.1)
        assert rig.bot.state.status == "off"
        assert rig.bot.state.fired_at  # el disparo cuenta para los topes
        rig.clock.now += 60_000
        rig.books()
        await asyncio.sleep(0.1)
        assert rig.bot.state.status == "off" and len(rig.orders.doubles) == 1


@pytest.mark.spec("RF-21")
async def test_apagado_mientras_dispara_y_acaba_mal_queda_parado(tmp_path):
    async def refuse(req):
        await asyncio.sleep(0.05)  # da tiempo a apagarlo con la doble en vuelo
        raise VenueError("Neoxa: [400] rechazada")

    async with BotRig(tmp_path) as rig:
        rig.venues["nx"].place_limit = refuse
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.bot.state.status == "firing")
        rig.bot.disarm()
        await rig.until(lambda: rig.bot.state.status == "halted")
        assert rig.bot.state.reason.startswith("rejected")


@pytest.mark.spec("RF-26", "CA-12")
class TestRatioDeInventario:
    """Saldos del rig: 1 BTC + 1000 BTCB2 por venue, ~87 % del valor en BTCB2."""

    def rig_with(self, tmp_path, target):
        rig = BotRig(tmp_path)
        rig.bot = BotService(
            rig.hub, rig.orders, rig.account, rig.journal, BotConfig(True, BOT),
            live=False, faults=rig.faults, clock=rig.clock, settle_s=1.0, tick_s=0.02,
            target=target,
        )  # fmt: skip
        return rig

    async def test_no_dispara_si_empeora_un_venue_y_lo_dice(self, tmp_path):
        async with self.rig_with(tmp_path, InventoryTarget(D("25"), D("10"))) as rig:
            rig.bot.arm()
            rig.books()  # comprar en NonKYC lo aleja aún más del 25 %
            await rig.until(lambda: rig.bot.last_skip is not None)
            assert rig.bot.last_skip.reason == "inventory_band"
            assert "inventory_band" in rig.bot.status_text()
            assert "nk: XBT" in rig.bot.status_text()
            assert rig.orders.doubles == {}

    async def test_sin_objetivo_dispara(self, tmp_path):
        async with self.rig_with(tmp_path, None) as rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: len(rig.orders.doubles) == 1)


NKYC = FeeToken("NKYC", D("1"), D("25"))
THIN = replace(BOT, max_slippage_pct=D("0"), min_net_pct=D("0.33"))
THIN_BOOKS = {"nk": ("0.00690000", "0.00700000"), "nx": ("0.00705000", "0.00730000")}


@pytest.mark.spec("RF-19", "RF-24")
class TestFeeConToken:
    """Neta del cruce fino: 0.31 % con la fee completa, 0.36 % con NKYC (D-63)."""

    async def test_con_token_dispara(self, tmp_path):
        tokens = {"nk": NKYC}
        nkyc = {"NKYC": Balance("NKYC", D("4.5"))}
        async with BotRig(tmp_path, limits=THIN, fee_tokens=tokens, nk_extra=nkyc) as rig:
            assert "NonKYC fee taker -25 % mientras haya 1 NKYC" in rig.bot.summary
            rig.bot.arm()
            rig.books(**THIN_BOOKS)
            await rig.until(lambda: rig.orders.doubles)
            assert "NonKYC fee con NKYC" in rig.bot.status_text()

    async def test_bajo_el_minimo_fee_completa(self, tmp_path):
        tokens = {"nk": NKYC}
        nkyc = {"NKYC": Balance("NKYC", D("0.5"), D("9"))}  # lo bloqueado no cuenta
        async with BotRig(tmp_path, limits=THIN, fee_tokens=tokens, nk_extra=nkyc) as rig:
            rig.bot.arm()
            rig.books(**THIN_BOOKS)
            await asyncio.sleep(0.1)
            assert rig.orders.doubles == {}
            assert rig.bot.last_skip.reason == "below_threshold"
            assert "NonKYC fee completa: 0.5 NKYC < 1" in rig.bot.status_text()

    async def test_sin_token_configurado_fee_completa(self, tmp_path):
        nkyc = {"NKYC": Balance("NKYC", D("4.5"))}
        async with BotRig(tmp_path, limits=THIN, nk_extra=nkyc) as rig:
            rig.bot.arm()
            rig.books(**THIN_BOOKS)
            await asyncio.sleep(0.1)
            assert rig.orders.doubles == {}
            assert "NKYC" not in rig.bot.status_text() + rig.bot.summary


@pytest.mark.spec("RF-19", "RF-23", "CA-08")
class TestMovimientoBrusco:
    """D-71: el bot mide el mid de cada venue en una ventana y no dispara tras
    un salto mayor que `max_move_pct`; el movimiento va en `bot_fire`."""

    async def test_tras_un_salto_espera_a_que_salga_de_la_ventana(self, tmp_path):
        limits = replace(BOT, max_move_pct=D("1"), move_window_ms=2000)
        async with BotRig(tmp_path, limits=limits) as rig:
            rig.books(nx=("0.00690000", "0.00705000"))  # sin cruce, mid 0.006975
            await rig.until(lambda: rig.hub.state["nx"].book.mid == D("0.006975"))
            rig.bot.arm()
            rig.books()  # Neoxa salta a mid 0.00725 (+3.9 %): cruza, pero brusco
            await rig.until(lambda: rig.bot.last_skip and rig.bot.last_skip.reason == "volatile")
            assert rig.bot.last_skip.detail.startswith("nx: mid 3.94 %")
            assert not rig.orders.doubles
            rig.clock.now += 2_001  # el salto sale de la ventana
            rig.books()
            await rig.until(lambda: rig.orders.doubles)
            (fire,) = rig.events("bot_fire")
            assert fire["data"]["move"] == {"nk": "0", "nx": "0"}

    async def test_salto_tras_un_book_quieto_cuenta(self, tmp_path):
        """D-72: si el book lleva más de la ventana quieto, el mid vigente al
        empezar la ventana sigue contando (d-60003f disparó con move 0)."""
        limits = replace(BOT, max_move_pct=D("1"), move_window_ms=2000)
        async with BotRig(tmp_path, limits=limits) as rig:
            rig.books(nx=("0.00690000", "0.00705000"))  # sin cruce, mid 0.006975
            await rig.until(lambda: rig.hub.state["nx"].book.mid == D("0.006975"))
            rig.bot.arm()
            rig.clock.now += 5_000  # el book se queda quieto más que la ventana
            rig.books()  # salta a mid 0.00725 (+3.9 %) en un solo cambio
            await rig.until(lambda: rig.bot.last_skip and rig.bot.last_skip.reason == "volatile")
            assert rig.bot.last_skip.detail.startswith("nx: mid 3.94 %")
            assert not rig.orders.doubles

    async def test_sin_filtro_dispara_y_apunta_el_movimiento(self, rig):
        rig.books(nx=("0.00690000", "0.00705000"))
        await rig.until(lambda: rig.hub.state["nx"].book.mid == D("0.006975"))
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (fire,) = rig.events("bot_fire")
        assert D(fire["data"]["move"]["nx"]) > D("3.9")


LEAD = replace(BOT, lead_venue="nx", fill_timeout_ms=2_000)


def fills_on_place(venue, qty):
    """El venue casa al recibir la orden, como Neoxa: llena `qty` antes del ack."""
    place = venue.place_limit

    async def placed(req):
        order = await place(req)
        if qty > 0:
            order = await venue.simulate_fill(order.order_id, qty)
        return order

    venue.place_limit = placed


@pytest.mark.spec("RF-07", "RF-20", "RF-21", "RF-23", "CA-08")
class TestPrimeraPierna:
    """Con `lead_venue` (D-75), la pierna de ese venue sale primero y la otra
    solo por lo que llenó. Ruta del rig: compra en NonKYC, venta en Neoxa."""

    async def fire(self, tmp_path, nx_fill, limits=LEAD, setup=None):
        rig = BotRig(tmp_path, limits=limits)
        fills_on_place(rig.venues["nx"], nx_fill)
        if setup is not None:
            setup(rig)
        await rig.__aenter__()
        rig.bot.arm()
        rig.books()
        await rig.until(lambda: rig.orders.doubles)
        (double,) = rig.orders.doubles.values()
        return rig, double

    async def test_llena_y_sale_la_otra(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("10"))
        try:
            await rig.until(lambda: rig.orders.doubles[double.id].buy.order_id)
            sent = [(e["venue"], D(e["data"]["qty"])) for e in rig.events("order_sent")]
            assert sent == [("nx", D("10")), ("nk", D("10"))]
            (fire,) = rig.events("bot_fire")
            assert fire["data"]["lead"] == "nx"
            nk = rig.orders.doubles[double.id].buy
            await rig.venues["nk"].simulate_fill(nk.order_id, D("10"))
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            assert rig.events("bot_miss") == [] and rig.events("hanging_leg") == []
        finally:
            await rig.__aexit__(None, None, None)

    async def test_en_parte_cancela_el_resto_y_la_otra_va_por_lo_lleno(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("4"))
        try:
            await rig.until(lambda: rig.orders.doubles[double.id].buy.order_id)
            d = rig.orders.doubles[double.id]
            assert d.buy.req.qty == D("4")
            assert d.sell.status == "cancelled" and d.sell.filled == D("4")
            assert [e["venue"] for e in rig.events("order_sent")] == ["nx", "nk"]
            # la cancelación de Neoxa sale antes que la pierna de NonKYC
            assert rig.events("cancel_sent")[0]["venue"] == "nx"
            await rig.venues["nk"].simulate_fill(d.buy.order_id, D("4"))
            await rig.until(lambda: rig.bot.state.status == "cooldown")
        finally:
            await rig.__aexit__(None, None, None)

    async def test_sin_llenar_no_sale_la_otra(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("0"))
        try:
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            d = rig.orders.doubles[double.id]
            assert d.sell.status == "cancelled" and d.buy.status == "skipped" and d.done
            assert [e["venue"] for e in rig.events("order_sent")] == ["nx"]
            (miss,) = rig.events("bot_miss")
            assert miss["data"]["double"] == double.id and miss["data"]["venue"] == "nx"
            assert rig.events("hanging_leg") == []
            rig.orders.check_doubles()
            assert rig.orders.hanging == {}
            assert await rig.open_orders() == {"nk": [], "nx": []}
        finally:
            await rig.__aexit__(None, None, None)

    async def test_sin_llenar_no_cuenta_como_fallo(self, tmp_path):
        limits = replace(LEAD, max_consecutive_failures=1, cooldown_s=2)
        rig, _ = await self.fire(tmp_path, D("0"), limits)
        try:
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            rig.clock.now += 2_000
            rig.books()
            await rig.until(lambda: len(rig.events("bot_miss")) == 2)
            await rig.until(lambda: rig.bot.state.status == "cooldown")
            assert rig.bot.state.failures == 0
        finally:
            await rig.__aexit__(None, None, None)

    async def test_rechazada_no_sale_la_otra(self, tmp_path):
        async def refuse(req):
            raise VenueError("Neoxa: [400] rechazada")

        rig = BotRig(tmp_path, limits=LEAD)
        rig.venues["nx"].place_limit = refuse
        async with rig:
            rig.bot.arm()
            rig.books()
            await rig.until(lambda: rig.bot.state.status == "halted")
            (double,) = rig.orders.doubles.values()
            assert double.buy.status == "skipped" and double.buy.order_id is None
            assert [e["venue"] for e in rig.events("order_sent")] == ["nx"]
            assert rig.bot.state.reason.startswith("rejected")

    async def test_la_cancelacion_falla_porque_ya_lleno(self, tmp_path):
        """Como el 2026-10-27 17:23: "Cannot cancel order with status: filled"."""
        def setup(rig):
            nx = rig.venues["nx"]

            async def too_late(order_id):
                await nx.simulate_fill(order_id, D("10"))
                raise VenueError("Neoxa: HTTP 400: Cannot cancel order with status: filled")

            nx.cancel = too_late

        rig, double = await self.fire(tmp_path, D("0"), setup=setup)
        try:
            await rig.until(lambda: rig.orders.doubles[double.id].buy.order_id)
            d = rig.orders.doubles[double.id]
            assert d.sell.status == "filled" and d.buy.req.qty == D("10")
            assert rig.events("bot_miss") == []
        finally:
            await rig.__aexit__(None, None, None)

    async def test_lo_lleno_bajo_el_lote_es_colgada(self, tmp_path):
        rig, double = await self.fire(tmp_path, D("0.00005"))  # lote de NonKYC: 0.0001
        try:
            await rig.until(lambda: rig.bot.state.status == "halted")
            d = rig.orders.doubles[double.id]
            assert d.buy.status == "skipped" and d.sell.filled == D("0.00005")
            assert rig.bot.state.reason.startswith("hanging_leg")
            assert [e["venue"] for e in rig.events("order_sent")] == ["nx"]
        finally:
            await rig.__aexit__(None, None, None)
