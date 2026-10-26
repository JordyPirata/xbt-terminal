"""Fallos inesperados: un bug o el disco lleno no cierran la terminal.

`Faults` es el registro común de hub, cuenta, órdenes y UI. `report` escribe en
el journal un `error` con el traceback (el `Journal` lo pasa por el `Redactor`),
guarda el fallo para la barra de estado y avisa a los suscriptores. Si el
journal tampoco se puede escribir, avisa igual: lo peor es no enterarse.

`supervised` mantiene viva una tarea de un venue: si lanza algo que no sea una
cancelación, lo reporta y la vuelve a arrancar con backoff exponencial (de
`retry_s` a `max_s`; vuelve a `retry_s` si la tarea aguantó más de `max_s`).
Los errores de red los sigue tratando cada servicio; aquí llegan los bugs.

Spec: RNF-14 · design.md §6.7 · Tarea: T-27 · D-28
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
        self.recent: list[Fault] = []  # los últimos KEPT
        self.count = 0  # en toda la sesión

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
        log.error("fallo en %s: %s", at, error, exc_info=exc)  # traceback redactado al escribir
        if self._journal is not None:
            trace = "".join(traceback.format_exception(exc))
            data = {"where": where, "error": error, "traceback": trace}
            if note:
                data["note"] = note
            try:
                self._journal.write("error", venue=venue, data=data)
            except (OSError, ValueError):  # disco lleno o fichero cerrado: se avisa igual
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
    """Hasta que `work` acabe bien o se cancele."""
    loop = asyncio.get_running_loop()
    delay = retry_s
    while True:
        started = loop.time()
        try:
            await work()
            return
        except Exception as exc:  # CancelledError no es Exception: se propaga
            on_crash(exc)
        if loop.time() - started > max_s:
            delay = retry_s
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_s)
