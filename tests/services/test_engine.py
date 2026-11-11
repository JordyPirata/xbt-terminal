"""UI-less engine (T-50, RF-30, D-61): the loops that used to live in the app (hanging
legs, reconciliation, clock skew and draining on close) run in `Engine` and come out
as `Notice`."""

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
class TestHangingLeg:
    async def test_warns_in_red_once(self, tmp_path):
        setup = double.Setup(tmp_path)
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)
        notices = heard(engine)
        sent = await setup.orders.confirm_double(await setup.orders.prepare_double(double.dbl()))
        order = await setup.venues["nk"].simulate_fill(sent.state.buy.order_id, D("0.01"))
        setup.orders.on_private_event("nk", order)
        setup.now += LIMITS.hanging_leg_after_s * 1000 + 1
        engine.check_doubles()
        engine.check_doubles()
        (notice,) = notices
        assert notice.error and notice.text.startswith("⚠ pierna colgada en d-000001")

    async def test_failure_goes_to_faults_and_does_not_cut(self, tmp_path):
        setup = double.Setup(tmp_path)
        faults = []
        setup.orders.faults.subscribe(faults.append)
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)

        def boom():
            raise RuntimeError("bug")

        setup.orders.check_doubles = boom
        engine.check_doubles()
        assert [fault.where for fault in faults] == ["engine"]


@pytest.mark.spec("RNF-14", "RF-30")
class TestFailures:
    """With distinct registries in orders and hub (D-78), the engine joins them."""

    def test_each_service_brings_its_own(self, tmp_path):
        setup = double.Setup(tmp_path)
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)
        assert setup.orders.faults is not setup.hub.faults
        assert engine.faults == [setup.orders.faults, setup.hub.faults]

    def test_report_fault_notes_in_orders_registry(self, tmp_path):
        setup = double.Setup(tmp_path)
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)
        fault = engine.report_fault("control", RuntimeError("bug"))
        assert fault.where == "control" and "bug" in fault.error
        assert setup.orders.faults.count == 1 and setup.hub.faults.count == 0

    def test_no_orders_notes_in_hub_registry(self, tmp_path):
        setup = double.Setup(tmp_path)
        engine = Engine(setup.hub, mode="dry-run")
        engine.report_fault("ui", RuntimeError("bug"))
        assert setup.hub.faults.count == 1

    def test_fault_count_adds_and_subscribe_faults_hears_all(self, tmp_path):
        setup = double.Setup(tmp_path)
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)
        seen = []
        engine.subscribe_faults(seen.append)
        setup.orders.faults.report("journal", RuntimeError("a"))
        setup.hub.faults.report("market", RuntimeError("b"))
        assert engine.fault_count == 2
        assert [fault.where for fault in seen] == ["journal", "market"]


@pytest.mark.spec("RNF-12", "RF-30")
class TestReconciliation:
    async def test_loop_reconciles_and_notifies(self, tmp_path):
        setup = Rig(tmp_path)
        setup.lose_reply()
        await setup.send(buy())
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders, reconcile_every_s=0.01)
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
        assert setup.orders.uncertain == {}

    async def test_without_uncertain_does_not_ask(self, tmp_path):
        setup = Rig(tmp_path)
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)
        assert await engine.reconcile() == []


@pytest.mark.spec("RNF-16", "RF-30")
class TestSkewCheck:
    @pytest.mark.parametrize(
        ("mode", "effect"),
        [("live", "no se envían órdenes a NonKYC"), ("dry-run", "en live no se podría")],
    )
    def test_warns_once_per_venue(self, tmp_path, mode, effect):
        setup = Rig(tmp_path)
        setup.hub._venues["nk"].clock_skew_ms = 9_000
        engine = Engine(setup.hub, mode=mode, orders=setup.orders, max_clock_skew_s=5)
        notices = heard(engine)
        engine.check_skew("nk")
        engine.check_skew("nk")
        (notice,) = notices
        assert notice.error and "reloj desfasado +9 s" in notice.text and effect in notice.text

    def test_within_cap_or_uncapped_stays_silent(self, tmp_path):
        setup = Rig(tmp_path)
        setup.hub._venues["nk"].clock_skew_ms = 9_000
        quiet = Engine(setup.hub, mode="live", orders=setup.orders)
        notices = heard(quiet)
        quiet.check_skew("nk")
        assert notices == []


@pytest.mark.spec("RNF-15", "RF-30")
class TestClose:
    async def test_waits_for_send_in_flight(self, tmp_path):
        setup = Rig(tmp_path)
        gate = asyncio.Event()
        real = setup.venue.place_limit

        async def slow(order_request):
            await gate.wait()
            return await real(order_request)

        setup.venue.place_limit = slow
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)
        sending = asyncio.create_task(setup.send(buy()))
        await asyncio.sleep(0)
        while not setup.orders.in_flight:
            await asyncio.sleep(0)
        closing = asyncio.create_task(engine.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done()
        gate.set()
        await closing
        await sending
        assert setup.events() == ["order_sent", "order_ack"]

    async def test_uncertain_error_in_flight_is_noted(self, tmp_path):
        setup = Rig(tmp_path)

        async def lost(order_request):
            await asyncio.sleep(0.01)
            raise UncertainError("NonKYC: sin respuesta (ReadTimeout)")

        setup.venue.place_limit = lost
        engine = Engine(setup.hub, mode="dry-run", orders=setup.orders)
        sending = asyncio.create_task(setup.send(buy()))
        while not setup.orders.in_flight:
            await asyncio.sleep(0)
        await engine.aclose()
        await sending
        assert setup.events() == ["order_sent", "order_unknown"]


@pytest.mark.spec("RF-32", "CA-17")
class TestAccountValue:
    def test_engine_samples_with_account_and_journal(self, tmp_path):
        from xbt_terminal.services.account import AccountService

        setup = Rig(tmp_path)
        account = AccountService({"nk": setup.venue}, journal=setup.journal)
        engine = Engine(
            setup.hub, mode="dry-run", account=account, journal=setup.journal, value_every_s=300
        )
        assert "value" in [name for name, _ in engine.services()]

    def test_without_account_no_sampling(self, tmp_path):
        setup = Rig(tmp_path)
        engine = Engine(setup.hub, mode="read-only", value_every_s=300)
        assert engine.values is None
        assert "value" not in [name for name, _ in engine.services()]
