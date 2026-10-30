"""La app con sus parámetros agrupados (T-84, D-77): la config que manda el
daemon en `hello` va entera en `view` y sus caras en `services`."""

import pytest

from xbt_terminal.core.inventory import InventoryTarget
from xbt_terminal.core.models import D
from xbt_terminal.services.engine import Engine
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.wire import ClientView
from xbt_terminal.ui.app import ClientServices, XbtApp

from .test_rebalance_ui import LIMITS


@pytest.mark.spec("RF-31")
def test_la_app_expone_lo_que_le_llega_agrupado():
    hub = MarketHub({})
    target = InventoryTarget(D("25"), D("10"))
    limits = LIMITS
    view = ClientView(20, "5m", {"nx": D("50")}, target, limits)
    services = ClientServices()
    app = XbtApp(
        hub, view=view, services=services, mode="dry-run", engine=Engine(hub, mode="dry-run")
    )
    assert (app.book_levels, app.timeframe) == (20, "5m")
    assert app.inventory_target is target and app.bot_limits is limits
    assert app._fee_discounts == {"nx": D("50")}
    assert app.orders is None and app.account is None and app.remote is None


@pytest.mark.spec("RF-31")
def test_sin_servicios_es_solo_lectura():
    hub = MarketHub({})
    app = XbtApp(
        hub,
        view=ClientView(15, "1m", {}, None, None),
        mode="dry-run",
        engine=Engine(hub, mode="dry-run"),
    )
    assert app.orders is None and app.bot is None and app.results is None
