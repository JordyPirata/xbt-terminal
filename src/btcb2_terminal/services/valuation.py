"""ValueRecorder: el daemon apunta el valor de la cuenta en el journal (RF-32).

Espera al siguiente múltiplo UTC de `every_s`, calcula el inventario con los
saldos de la cuenta y el mid del hub (como el panel de balances, RF-05) y, si
el total existe, escribe un evento `value`. Si falta el saldo o el mid de algún
venue, esa muestra no se apunta. Un fallo se reporta y el muestreo sigue.

Spec: RF-05, RF-32, RNF-14 · design.md §6.5 · D-69 · Tarea: T-69
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..adapters.streaming import now_ms
from ..core.inventory import inventory
from ..core.journal import Journal
from ..core.valuation import ValueSample, sample, sample_data
from .faults import Faults

VALUE_EVERY_S = 300


class _Account(Protocol):
    state: dict  # VenueId → estado con `balances`


class _Hub(Protocol):
    state: dict  # VenueId → estado con `book.mid`


class ValueRecorder:
    def __init__(
        self,
        account: _Account,
        hub: _Hub,
        journal: Journal,
        *,
        every_s: int = VALUE_EVERY_S,
        clock: Callable[[], int] = now_ms,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        faults: Faults | None = None,
    ) -> None:
        self._account = account
        self._hub = hub
        self._journal = journal
        self._every_ms = every_s * 1000
        self._clock = clock
        self._sleep = sleep
        self._faults = faults

    async def run(self) -> None:
        while True:
            now = self._clock()
            at = (now // self._every_ms + 1) * self._every_ms
            await self._sleep((at - now) / 1000)
            try:
                self.record(at)
            except Exception as exc:  # un bug o el disco lleno no paran el muestreo
                if self._faults is None:
                    raise
                self._faults.report("value", exc)

    def record(self, at: int) -> ValueSample | None:
        """Apunta la muestra de `at` si la hay."""
        balances = {vid: st.balances for vid, st in self._account.state.items()}
        mids = {vid: self._hub.state[vid].book.mid for vid in balances}
        s = sample(inventory(balances, mids), mids, at, self._every_ms)
        if s is not None:
            self._journal.write("value", data=sample_data(s))
        return s
