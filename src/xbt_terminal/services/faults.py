"""Unexpected faults: a bug or a full disk do not close the terminal.

`Faults` is the registry shared by hub, account, orders and UI. `report` writes an
`error` with the traceback to the journal (the `Journal` runs it through the
`Redactor`), keeps the fault for the status bar and notifies subscribers. If the
journal cannot be written either, it notifies anyway: the worst is not finding out.

`supervised` keeps a venue task alive: if it raises anything but a cancellation, it
reports and restarts it with exponential backoff (from `retry_s` to `max_s`; back to
`retry_s` if the task lasted more than `max_s`). Each service still handles network
errors; bugs land here.

Spec: RNF-14 · design.md §6.7 · Task: T-27 · D-28
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable

from ..adapters.streaming import now_ms
from ..core.journal import Journal
from ..core.models import VenueId
from ..core.notices import Fault
from ..core.secrets import Redactor

log = logging.getLogger(__name__)

MAX_RETRY_S = 60.0
KEPT = 20

class Faults:
    def __init__(self, journal: Journal | None = None, *, clock: Callable[[], int] = now_ms):
        self._journal = journal
        self._clock = clock
        self._listeners: list[Callable[[Fault], None]] = []
        self.recent: list[Fault] = []  # the last KEPT
        self.count = 0  # over the whole session

    def subscribe(self, listener: Callable[[Fault], None]) -> None:
        self._listeners.append(listener)

    def report(
        self, where: str, exc: BaseException, *, venue: VenueId | None = None,
        note: str | None = None,
    ) -> Fault:  # fmt: skip
        redactor = self._journal.redactor if self._journal is not None else Redactor([])
        error = redactor.redact(f"{type(exc).__name__}: {exc}")
        fault = Fault(where, venue, error, self._clock(), note)
        at = f"{where} ({venue})" if venue else where
        log.error("fault in %s: %s", at, error, exc_info=exc)  # traceback redacted on writing
        if self._journal is not None:
            trace = "".join(traceback.format_exception(exc))
            data = {"where": where, "error": error, "traceback": trace}
            if note:
                data["note"] = note
            try:
                self._journal.write("error", venue=venue, data=data)
            except (OSError, ValueError):  # disk full or closed file: notify anyway
                pass
        self.recent = [*self.recent, fault][-KEPT:]
        self.count += 1
        for listener in self._listeners:
            listener(fault)
        return fault


async def supervised(
    work: Callable[[], Awaitable[None]],
    on_crash: Callable[[Exception], None],
    *,
    retry_s: float,
    max_s: float = MAX_RETRY_S,
) -> None:
    """Until `work` ends well or is cancelled."""
    loop = asyncio.get_running_loop()
    delay = retry_s
    while True:
        started = loop.time()
        try:
            await work()
            return
        except Exception as error:  # CancelledError is not an Exception: it propagates
            on_crash(error)
        if loop.time() - started > max_s:
            delay = retry_s
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_s)
