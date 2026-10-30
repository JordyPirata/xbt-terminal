"""Motor sin UI (T-50, RF-30, D-61): los bucles que antes vivían en la app
(piernas colgadas, reconciliación de inciertas, desfase de reloj y drenaje al
cerrar) corren en `Engine` y salen como `Notice`."""

import asyncio

import pytest

from xbt_terminal.adapters.base import UncertainError
from xbt_terminal.core.models import D
from xbt_terminal.services.engine import Engine, Notice

from . import test_double_orders as double
from .test_orders import LIMITS
from .test_uncertain import Rig, buy


def heard(engine):
    out: list[Notice] = []
    engine.subscribe(out.append)
    return out


@pytest.mark.spec("RF-07", "RF-30")
class TestPiernaColgada:
    async def test_avisa_en_rojo_una_vez(self, tmp_path):
        s = double.Setup(tmp_path)
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)
        notices = heard(engine)
        sent = await s.orders.confirm_double(await s.orders.prepare_double(double.dbl()))
        order = await s.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        s.orders.on_private_event("nk", order)
        s.now += LIMITS.hanging_leg_after_s * 1000 + 1
        engine.check_doubles()
        engine.check_doubles()
        (notice,) = notices
        assert notice.error and notice.text.startswith("⚠ pierna colgada en d-000001")

    async def test_un_fallo_va_a_faults_y_no_corta(self, tmp_path):
        s = double.Setup(tmp_path)
        faults = []
        s.orders.faults.subscribe(faults.append)
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)

        def boom():
            raise RuntimeError("bug")

        s.orders.check_doubles = boom
        engine.check_doubles()
        assert [f.where for f in faults] == ["engine"]


@pytest.mark.spec("RNF-14", "RF-30")
class TestFallos:
    """Con registros distintos en órdenes y hub (D-78), el motor los junta."""

    def test_cada_servicio_trae_el_suyo(self, tmp_path):
        s = double.Setup(tmp_path)
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)
        assert s.orders.faults is not s.hub.faults
        assert engine.faults == [s.orders.faults, s.hub.faults]

    def test_report_fault_apunta_en_el_de_ordenes(self, tmp_path):
        s = double.Setup(tmp_path)
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)
        fault = engine.report_fault("control", RuntimeError("bug"))
        assert fault.where == "control" and "bug" in fault.error
        assert s.orders.faults.count == 1 and s.hub.faults.count == 0

    def test_sin_ordenes_apunta_en_el_del_hub(self, tmp_path):
        s = double.Setup(tmp_path)
        engine = Engine(s.hub, mode="dry-run")
        engine.report_fault("ui", RuntimeError("bug"))
        assert s.hub.faults.count == 1

    def test_fault_count_suma_y_subscribe_faults_oye_todos(self, tmp_path):
        s = double.Setup(tmp_path)
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)
        seen = []
        engine.subscribe_faults(seen.append)
        s.orders.faults.report("journal", RuntimeError("a"))
        s.hub.faults.report("market", RuntimeError("b"))
        assert engine.fault_count == 2
        assert [f.where for f in seen] == ["journal", "market"]


@pytest.mark.spec("RNF-12", "RF-30")
class TestReconciliacion:
    async def test_el_bucle_reconcilia_y_avisa(self, tmp_path):
        s = Rig(tmp_path)
        s.lose_reply()
        await s.send(buy())
        engine = Engine(s.hub, mode="dry-run", orders=s.orders, reconcile_every_s=0.01)
        notices = heard(engine)
        task = asyncio.create_task(engine.watch_uncertain())
        try:
            for _ in range(100):
                if notices:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
        (notice,) = notices
        assert "existe" in notice.text and not notice.error
        assert s.orders.uncertain == {}

    async def test_sin_inciertas_no_pregunta(self, tmp_path):
        s = Rig(tmp_path)
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)
        assert await engine.reconcile() == []


@pytest.mark.spec("RNF-16", "RF-30")
class TestDesfase:
    @pytest.mark.parametrize(
        ("mode", "effect"),
        [("live", "no se envían órdenes a NonKYC"), ("dry-run", "en live no se podría")],
    )
    def test_avisa_una_vez_por_venue(self, tmp_path, mode, effect):
        s = Rig(tmp_path)
        s.hub._venues["nk"].clock_skew_ms = 9_000
        engine = Engine(s.hub, mode=mode, orders=s.orders, max_clock_skew_s=5)
        notices = heard(engine)
        engine.check_skew("nk")
        engine.check_skew("nk")
        (notice,) = notices
        assert notice.error and "reloj desfasado +9 s" in notice.text and effect in notice.text

    def test_dentro_del_tope_o_sin_tope_calla(self, tmp_path):
        s = Rig(tmp_path)
        s.hub._venues["nk"].clock_skew_ms = 9_000
        quiet = Engine(s.hub, mode="live", orders=s.orders)
        notices = heard(quiet)
        quiet.check_skew("nk")
        assert notices == []


@pytest.mark.spec("RNF-15", "RF-30")
class TestCerrar:
    async def test_espera_al_envio_en_vuelo(self, tmp_path):
        s = Rig(tmp_path)
        gate = asyncio.Event()
        real = s.venue.place_limit

        async def slow(req):
            await gate.wait()
            return await real(req)

        s.venue.place_limit = slow
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)
        sending = asyncio.create_task(s.send(buy()))
        await asyncio.sleep(0)
        while not s.orders.in_flight:
            await asyncio.sleep(0)
        closing = asyncio.create_task(engine.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done()
        gate.set()
        await closing
        await sending
        assert s.events() == ["order_sent", "order_ack"]

    async def test_error_incierto_en_vuelo_queda_apuntado(self, tmp_path):
        s = Rig(tmp_path)

        async def lost(req):
            await asyncio.sleep(0.01)
            raise UncertainError("NonKYC: sin respuesta (ReadTimeout)")

        s.venue.place_limit = lost
        engine = Engine(s.hub, mode="dry-run", orders=s.orders)
        sending = asyncio.create_task(s.send(buy()))
        while not s.orders.in_flight:
            await asyncio.sleep(0)
        await engine.aclose()
        await sending
        assert s.events() == ["order_sent", "order_unknown"]


@pytest.mark.spec("RF-32", "CA-17")
class TestValorDeLaCuenta:
    def test_el_motor_muestrea_si_hay_cuenta_y_journal(self, tmp_path):
        from xbt_terminal.services.account import AccountService

        s = Rig(tmp_path)
        account = AccountService({"nk": s.venue}, journal=s.journal)
        engine = Engine(
            s.hub, mode="dry-run", account=account, journal=s.journal, value_every_s=300
        )
        assert "value" in [name for name, _ in engine.services()]

    def test_sin_cuenta_no_muestrea(self, tmp_path):
        s = Rig(tmp_path)
        engine = Engine(s.hub, mode="read-only", value_every_s=300)
        assert engine.values is None
        assert "value" not in [name for name, _ in engine.services()]
