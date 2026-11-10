"""Engine without a screen: what the terminal does even if nobody watches.

Groups hub, account, orders and bot, and runs the loops that used to live in the app:
hanging legs every second (RF-07), reconciliation of uncertain orders (RNF-12), skewed
clock warning (RNF-16) and, on closing, waiting for sends in flight (RNF-15). What the
terminal would say on screen comes out as `Notice` to subscribers: the app shows it
and the daemon notes it.

Spec: RF-30, RNF-12, RNF-15, RNF-16 · design.md §6.13 · Task: T-50 · D-61
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..adapters.streaming import now_ms
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

RECONCILE_EVERY_S = 3.0  # search for uncertain orders (RNF-12)
DOUBLES_EVERY_S = 1.0  # hanging legs (RF-07)


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
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.hub = hub
        # what the client asks for over the socket without trading (D-66)
        self.results = results
        self.deposits = deposits
        self.rebalance = rebalance
        self.mode = mode
        self.journal = journal  # the orders' one; None in read-only
        self.orders = orders
        self.account = account
        self.bot = bot
        self._max_skew_s = max_clock_skew_s  # None: not checked
        self._reconcile_every_s = reconcile_every_s
        self._skew_warned: set[VenueId] = set()
        self._listeners: list[Callable[[Notice], None]] = []
        # fault registries of orders, account and hub (one shared in the real app)
        found = (service.faults for service in (orders, account, hub) if service is not None)
        self.faults: list[Faults] = list({id(registry): registry for registry in found}.values())
        # account value in the journal (RF-32, D-69): needs the account
        self.values: ValueRecorder | None = None
        if value_every_s is not None and account is not None and journal is not None:
            self.values = ValueRecorder(
                account, hub, journal, every_s=value_every_s,
                faults=self._main_faults, clock=clock,
            )  # fmt: skip
        hub.subscribe(self._on_hub)
        if orders is not None and account is not None:
            # account orders update the legs of doubles (T-18). This lived in the app: without
            # it the daemon did not see fills (D-61, d-68e3ed)
            account.subscribe_events(orders.on_private_event)

    def subscribe(self, listener: Callable[[Notice], None]) -> None:
        self._listeners.append(listener)

    # -- faults (RNF-14, D-78) ----------------------------------------------------

    def report_fault(self, where: str, error: BaseException) -> Fault:
        """Note a fault in the main registry: the orders' if any, else the account's, else
        the hub's (there is always one)."""
        return self._main_faults.report(where, error)

    @property
    def _main_faults(self) -> Faults:
        return self.faults[0]  # the hub always brings one: never empty

    @property
    def fault_count(self) -> int:
        """Session faults, added across all registries."""
        return sum(faults.count for faults in self.faults)

    def subscribe_faults(self, listener: Callable[[Fault], None]) -> None:
        for faults in self.faults:
            faults.subscribe(listener)

    def _say(self, text: str, *, error: bool = False) -> None:
        notice = Notice(text, error)
        for listener in self._listeners:
            listener(notice)

    # -- tasks -------------------------------------------------------------------

    def services(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        """Market, account, bot and value sampling, each named; each service isolates its
        faults (RNF-14), so whoever runs them does not supervise them."""
        out: list[tuple[str, Callable[[], Awaitable[None]]]] = [("market-hub", self.hub.run)]
        if self.account is not None:
            out.append(("account", self.account.run))
        if self.bot is not None:
            out.append(("bot", self.bot.run))
        if self.values is not None:
            out.append(("value", self.values.run))
        return out

    def watchers(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        """Hanging legs and reconciliation; without orders, none."""
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
            except Exception as error:  # a bug here cannot stop the search
                self.report_fault("reconcile", error)

    # -- hanging legs (RF-07) -------------------------------------------------

    def check_doubles(self) -> None:
        if self.orders is None:
            return
        try:
            new = self.orders.check_doubles()
        except Exception as exception:  # every second: a bug here cannot close anything
            self.report_fault("engine", exception)
            return
        for double_id in new:
            self._say(
                f"⚠ pierna colgada en {double_id}: {self.orders.hanging[double_id]}", error=True
            )

    # -- uncertain sends (RNF-12) ------------------------------------------------

    async def reconcile(self) -> list[Reconciled]:
        if self.orders is None or not self.orders.uncertain:
            return []
        results = await self.orders.reconcile()
        for reconciled in results:
            self._say(reconciled.message, error=reconciled.error)
        return results

    # -- clock (RNF-16, D-30) -----------------------------------------------------

    def _on_hub(self, venue: VenueId, what: str) -> None:
        if what == "market":
            self.check_skew(venue)

    def check_skew(self, venue: VenueId) -> None:
        """Once per venue, on loading its market: the skew is measured by then."""
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

    # -- closing (RNF-15, D-29) ----------------------------------------------------

    async def aclose(self) -> None:
        """Sends and cancels in flight, to the journal; then everything closes."""
        if self.orders is not None:
            await self.orders.drain()
            self.orders.close()
        await self.hub.aclose()
