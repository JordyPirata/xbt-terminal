"""Rebalance plan window (T-45, RF-29, CA-14): `e` key, `rebalance` command, both
alternatives with their cost or why not viable, the recommended one, reload on
reopening and notices without keys or balances."""

import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.core.bot import BotLimits
from xbt_terminal.core.inventory import InventoryTarget
from xbt_terminal.core.models import Balance, D
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.rebalance import RebalanceService
from xbt_terminal.ui.screens.rebalance import RebalanceScreen

from ..fakes import FakeFeed
from ..services.test_rebalance_service import FakeFees
from ..unit.test_rebalance import NK, NX
from .local import local_app
from .test_orders_ui import Rig, type_command, until

LIMITS = BotLimits(
    min_net_pct=D("0.5"), max_slippage_pct=D("0.2"), max_qty=D("0.05"), fill_timeout_ms=3000,
    cooldown_s=30, max_trades_per_hour=10, max_daily_btc=D("0.01"), max_consecutive_failures=3,
)  # fmt: skip


def make_app(service, holdings=None, **opts):
    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    app = local_app(
        MarketHub(feeds), book_levels=15, timeframe="1m", mode="read-only", rebalance=service,
        **opts,
    )  # fmt: skip
    if holdings is not None:
        app.rebalance_holdings = lambda: holdings  # type: ignore[method-assign]
    return app


def service(**fail):
    return RebalanceService({"nk": FakeFees("nk", fail=fail), "nx": FakeFees("nx")})


def screen(app) -> RebalanceScreen:
    assert isinstance(app.screen, RebalanceScreen)
    return app.screen


async def opened(app, pilot):
    await until(pilot, lambda: isinstance(app.screen, RebalanceScreen))
    await until(pilot, lambda: screen(app).plan is not None)
    return screen(app)


@pytest.mark.spec("RF-29", "CA-14")
class TestScreen:
    async def test_e_key_shows_plan_and_esc_closes(self):
        app = make_app(service(), {"nk": NK, "nx": NX}, bot_limits=LIMITS)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("e")
            rebalance_screen = await opened(app, pilot)
            text = rebalance_screen.text()
            assert "NonKYC" in text and "62.3 %" in text and "1.4 %" in text
            xbt = next(ln for ln in text.splitlines() if "XBT" in ln and "→" in ln)
            assert "NonKYC → Neoxa" in xbt and "0.0027" in xbt and "1 485 sats" in xbt
            assert "recomendada" in text.lower() and "11 dobles" in text
            assert "solo calcula" in text.lower()
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, RebalanceScreen))

    async def test_not_viable_says_why(self):
        app = make_app(service(BTCB2=VenueError("NonKYC: HTTP 500")), {"nk": NK, "nx": NX})
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("e")
            rebalance_screen = await opened(app, pilot)
            xbt = next(
                ln for ln in rebalance_screen.text().splitlines() if "XBT" in ln and "→" in ln
            )
            assert "HTTP 500" in xbt
            assert (
                rebalance_screen.plan.best is not None and rebalance_screen.plan.best.asset == "BTC"
            )

    async def test_warns_if_total_is_outside_band(self):
        target = InventoryTarget(D("50"), D("10"))  # the total is at 25.5 %
        app = make_app(service(), {"nk": NK, "nx": NX}, inventory_target=target)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("e")
            rebalance_screen = await opened(app, pilot)
            assert (
                "fuera de la banda" in rebalance_screen.text()
                and "no lo cambia" in rebalance_screen.text()
            )

    async def test_no_warning_if_total_in_band(self):
        target = InventoryTarget(D("25"), D("10"))
        app = make_app(service(), {"nk": NK, "nx": NX}, inventory_target=target)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("e")
            rebalance_screen = await opened(app, pilot)
            assert "fuera de la banda" not in rebalance_screen.text()

    async def test_reopening_and_r_ask_again(self):
        nk = FakeFees("nk")
        app = make_app(RebalanceService({"nk": nk, "nx": FakeFees("nx")}), {"nk": NK, "nx": NX})
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("e")
            await opened(app, pilot)
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, RebalanceScreen))
            await pilot.press("e")
            await opened(app, pilot)
            await pilot.press("r")
            await until(pilot, lambda: len(nk.calls) == 6)

    async def test_rebalance_command(self, tmp_path):
        rig = Rig(tmp_path, rebalance=service())
        rig.app.rebalance_holdings = lambda: {"nk": NK, "nx": NX}  # type: ignore[method-assign]
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await type_command(pilot, rig.app, "rebalance")
            await opened(rig.app, pilot)

    async def test_no_balances_says_so(self):
        app = make_app(service(), "nx: sin saldos")
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("e")
            await until(pilot, lambda: isinstance(app.screen, RebalanceScreen))
            await until(pilot, lambda: "sin saldos" in screen(app).text())
            assert screen(app).plan is None

    async def test_no_keys_says_so(self):
        app = make_app(None)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("e")
            await pilot.pause()
            assert not isinstance(app.screen, RebalanceScreen)
            assert app.last_error and "keys" in app.last_message


@pytest.mark.spec("RF-29")
class TestAppBalances:
    def test_without_account(self):
        assert "saldos" in make_app(service()).rebalance_holdings()

    async def test_with_account_and_mids(self, tmp_path):
        from xbt_terminal.services.account import AccountService

        from .test_orders_ui import ready

        rig = Rig(tmp_path)
        account = AccountService({"nk": rig.venue})
        rig.app.account = account
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            account.state["nk"].balances = {
                "BTC": Balance("BTC", D("0.005")), "BTCB2": Balance("BTCB2", D("0.5")),
            }  # fmt: skip
            got = rig.app.rebalance_holdings()
            # without Neoxa balances there is no plan, and the message says so
            assert isinstance(got, str) and "nx" in got
