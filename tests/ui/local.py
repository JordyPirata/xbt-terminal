"""La app con los servicios de verdad en el mismo proceso, para los tests de la
pantalla. En producción la app es el cliente del daemon (D-66) y no crea su
`Engine`; aquí se le da uno con las mismas piezas que corre el daemon."""

from xbt_terminal.services.engine import RECONCILE_EVERY_S, Engine
from xbt_terminal.ui.app import XbtApp


def local_app(hub, *, reconcile_every_s=RECONCILE_EVERY_S, max_clock_skew_s=None, **kw):
    if kw.get("engine") is None:
        remote = kw.get("remote")
        kw["engine"] = Engine(
            hub, mode=kw["mode"], orders=kw.get("orders"),
            account=kw.get("account") if remote is None else None,
            bot=kw.get("bot") if remote is None else None,
            max_clock_skew_s=max_clock_skew_s, reconcile_every_s=reconcile_every_s,
        )  # fmt: skip
    return XbtApp(hub, **kw)
