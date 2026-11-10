"""ValueRecorder: the daemon notes the account value in the journal (RF-32).

Waits for the next UTC multiple of `every_s`, computes the inventory with the account
balances and the hub mid (like the balance panel, RF-05) and, if the total exists,
writes a `value` event. If a venue lacks its balance or mid, that sample is not
written. A fault is reported and sampling goes on.

Spec: RF-05, RF-32, RNF-14 · design.md §6.5 · D-69 · Task: T-69
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
    state: dict  # VenueId → state with `balances`


class _Hub(Protocol):
    state: dict  # VenueId → state with `book.mid`


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
            except Exception as error:  # a bug or a full disk do not stop sampling
                if self._faults is None:
                    raise
                self._faults.report("value", error)

    def record(self, at: int) -> ValueSample | None:
        """Write the `at` sample if there is one."""
        balances = {venue_id: state.balances for venue_id, state in self._account.state.items()}
        mids = {venue_id: self._hub.state[venue_id].book.mid for venue_id in balances}
        value_sample = sample(inventory(balances, mids), mids, at, self._every_ms)
        if value_sample is not None:
            self._journal.write("value", data=sample_data(value_sample))
        return value_sample
