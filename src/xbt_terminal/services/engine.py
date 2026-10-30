"""Motor sin pantalla: lo que la terminal hace aunque nadie la mire.

Agrupa hub, cuenta, órdenes y bot, y corre los bucles que antes vivían en la
app: piernas colgadas cada segundo (RF-07), reconciliación de inciertas
(RNF-12), aviso de reloj desfasado (RNF-16) y, al cerrar, la espera a los
envíos en vuelo (RNF-15). Lo que la terminal diría en pantalla sale como
`Notice` a quien se suscriba: la app lo muestra y el daemon lo apunta.

Spec: RF-30, RNF-12, RNF-15, RNF-16 · design.md §6.13 · Tarea: T-50 · D-61
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..core.journal import Journal
from ..core.models import VenueId
from ..core.notices import VENUE, Notice
from .account import AccountService
from .bot import BotService
from .deposits import DepositService
from .faults import Fault, Faults
from .market import MarketHub
from .orders import OrderService, Reconciled
from .rebalance import RebalanceService
from .results import ResultsService
from .valuation import ValueRecorder

RECONCILE_EVERY_S = 3.0  # búsqueda de órdenes inciertas (RNF-12)
DOUBLES_EVERY_S = 1.0  # piernas colgadas (RF-07)


class Engine:
    def __init__(
        self,
        hub: MarketHub,
        *,
        mode: str,
        orders: OrderService | None = None,
        account: AccountService | None = None,
        bot: BotService | None = None,
        max_clock_skew_s: int | None = None,
        reconcile_every_s: float = RECONCILE_EVERY_S,
        journal: Journal | None = None,
        results: ResultsService | None = None,
        deposits: DepositService | None = None,
        rebalance: RebalanceService | None = None,
        value_every_s: int | None = None,
    ) -> None:
        self.hub = hub
        # lo que el cliente pide por el socket sin que sea operar (D-66)
        self.results = results
        self.deposits = deposits
        self.rebalance = rebalance
        self.mode = mode
        self.journal = journal  # el de órdenes; None en solo lectura
        self.orders = orders
        self.account = account
        self.bot = bot
        self._max_skew_s = max_clock_skew_s  # None: no se comprueba
        self._reconcile_every_s = reconcile_every_s
        self._skew_warned: set[VenueId] = set()
        self._listeners: list[Callable[[Notice], None]] = []
        # registros de fallos de órdenes, cuenta y hub (en la app real, uno compartido)
        found = (s.faults for s in (orders, account, hub) if s is not None)
        self.faults: list[Faults] = list({id(f): f for f in found}.values())
        # valor de la cuenta en el journal (RF-32, D-69): hace falta la cuenta
        self.values: ValueRecorder | None = None
        if value_every_s is not None and account is not None and journal is not None:
            self.values = ValueRecorder(
                account, hub, journal, every_s=value_every_s,
                faults=self._main_faults,
            )  # fmt: skip
        hub.subscribe(self._on_hub)
        if orders is not None and account is not None:
            # las órdenes de la cuenta actualizan las piernas de las dobles (T-18).
            # Vivía en la app: sin esto el daemon no veía los fills (D-61, d-68e3ed)
            account.subscribe_events(orders.on_private_event)

    def subscribe(self, listener: Callable[[Notice], None]) -> None:
        self._listeners.append(listener)

    # -- fallos (RNF-14, D-78) ----------------------------------------------------

    def report_fault(self, where: str, exc: BaseException) -> Fault:
        """Apunta un fallo en el registro principal: el de órdenes si hay, si no el
        de la cuenta, si no el del hub (siempre hay uno)."""
        return self._main_faults.report(where, exc)

    @property
    def _main_faults(self) -> Faults:
        return self.faults[0]  # el hub siempre trae uno: nunca está vacía

    @property
    def fault_count(self) -> int:
        """Fallos de la sesión, sumados entre todos los registros."""
        return sum(f.count for f in self.faults)

    def subscribe_faults(self, listener: Callable[[Fault], None]) -> None:
        for faults in self.faults:
            faults.subscribe(listener)

    def _say(self, text: str, *, error: bool = False) -> None:
        notice = Notice(text, error)
        for listener in self._listeners:
            listener(notice)

    # -- tareas -------------------------------------------------------------------

    def services(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        """Mercado, cuenta, bot y muestreo del valor, cada uno con su nombre; cada
        servicio aísla sus fallos (RNF-14), así que quien las corra no las supervisa."""
        out: list[tuple[str, Callable[[], Awaitable[None]]]] = [("market-hub", self.hub.run)]
        if self.account is not None:
            out.append(("account", self.account.run))
        if self.bot is not None:
            out.append(("bot", self.bot.run))
        if self.values is not None:
            out.append(("value", self.values.run))
        return out

    def watchers(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        """Piernas colgadas y reconciliación; sin órdenes, ninguno."""
        if self.orders is None:
            return []
        return [("doubles", self.watch_doubles), ("reconcile", self.watch_uncertain)]

    async def watch_doubles(self) -> None:
        while True:
            await asyncio.sleep(DOUBLES_EVERY_S)
            self.check_doubles()

    async def watch_uncertain(self) -> None:
        while True:
            await asyncio.sleep(self._reconcile_every_s)
            try:
                await self.reconcile()
            except Exception as exc:  # un bug aquí no puede parar la búsqueda
                self.report_fault("reconcile", exc)

    # -- piernas colgadas (RF-07) -------------------------------------------------

    def check_doubles(self) -> None:
        if self.orders is None:
            return
        try:
            new = self.orders.check_doubles()
        except Exception as exc:  # cada segundo: un bug aquí no puede cerrar nada
            self.report_fault("engine", exc)
            return
        for double_id in new:
            self._say(
                f"⚠ pierna colgada en {double_id}: {self.orders.hanging[double_id]}", error=True
            )

    # -- envíos inciertos (RNF-12) ------------------------------------------------

    async def reconcile(self) -> list[Reconciled]:
        if self.orders is None or not self.orders.uncertain:
            return []
        results = await self.orders.reconcile()
        for r in results:
            self._say(r.message, error=r.error)
        return results

    # -- reloj (RNF-16, D-30) -----------------------------------------------------

    def _on_hub(self, venue: VenueId, what: str) -> None:
        if what == "market":
            self.check_skew(venue)

    def check_skew(self, venue: VenueId) -> None:
        """Una vez por venue, al cargar su mercado: el desfase ya está medido."""
        skew = self.hub.clock_skew_ms(venue)
        cap = self._max_skew_s
        if cap is None or skew is None or abs(skew) <= cap * 1000:
            return
        if venue in self._skew_warned:
            return
        self._skew_warned.add(venue)
        name = VENUE[venue]
        effect = (
            f"no se envían órdenes a {name}"
            if self.mode == "live"
            else "en live no se podría operar"
        )
        self._say(
            f"⚠ reloj desfasado {skew / 1000:+.0f} s con {name} (máx. {cap} s): {effect}. "
            "Sincroniza el reloj del sistema (NTP)",
            error=True,
        )

    # -- cerrar (RNF-15, D-29) ----------------------------------------------------

    async def aclose(self) -> None:
        """Envíos y cancelaciones en vuelo, al journal; después se cierra todo."""
        if self.orders is not None:
            await self.orders.drain()
            self.orders.close()
        await self.hub.aclose()
