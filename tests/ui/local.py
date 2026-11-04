"""The app with the real services in the same process, for screen tests. In production
the app is the daemon's client (D-66) and creates no `Engine`; here it gets one with
the same pieces the daemon runs.

Accepts the loose parameters from before T-84 and groups them into `view` and
`services`, so tests do not depend on how the app takes them."""

from xbt_terminal.services.engine import RECONCILE_EVERY_S, Engine
from xbt_terminal.services.wire import ClientView
from xbt_terminal.ui.app import ClientServices, XbtApp

SERVICES = ("orders", "account", "bot", "results", "deposits", "rebalance", "remote")


def local_app(
    hub,
    *,
    book_levels,
    timeframe,
    fee_discounts=None,
    inventory_target=None,
    bot_limits=None,
    reconcile_every_s=RECONCILE_EVERY_S,
    max_clock_skew_s=None,
    **kw,
):
    services = ClientServices(**{name: kw.pop(name) for name in SERVICES if name in kw})
    if kw.get("engine") is None:
        kw["engine"] = Engine(
            hub, mode=kw["mode"], orders=services.orders,
            account=services.account if services.remote is None else None,
            bot=services.bot if services.remote is None else None,
            max_clock_skew_s=max_clock_skew_s, reconcile_every_s=reconcile_every_s,
        )  # fmt: skip
    view = ClientView(book_levels, timeframe, fee_discounts or {}, inventory_target, bot_limits)
    return XbtApp(hub, view=view, services=services, **kw)
