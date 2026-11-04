"""Daemon: the engine without a screen, in the foreground, until asked to stop.

It knows nothing of the service manager launching it (D-61): whoever supervises it
sends SIGTERM or SIGINT (`__main__` turns them into `stop`). On stopping, it disarms
the bot, waits for sends and cancels in flight (RNF-15) and, in live, notes any open
orders: there is nobody to ask. Notices the terminal would show go to the diagnostic
log, and error ones to the journal too, as `notice`.

Spec: RF-30, RNF-15 · design.md §6.13 · Task: T-51 · D-61
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from .control import BOOK_DEPTH, ControlServer
from .engine import Engine, Notice
from .wire import ClientView

log = logging.getLogger(__name__)


async def run_daemon(
    engine: Engine,
    stop: asyncio.Event,
    *,
    on_ready: Callable[[], None] | None = None,
    socket: Path | None = None,
    book_depth: int = BOOK_DEPTH,
    view: ClientView | None = None,
) -> None:
    """With `socket`, opens the control one for the `xbt` client (RF-31); the book travels
    with `book_depth` levels per side (D-66)."""

    def heard(notice: Notice) -> None:
        if not notice.error:
            log.info("%s", notice.text)
            return
        log.warning("%s", notice.text)
        _record(engine, notice.text)

    engine.subscribe(heard)
    control = (
        ControlServer(engine, socket, book_depth=book_depth, view=view)
        if socket is not None
        else None
    )
    if control is not None:
        await control.start()  # before the tasks: if it fails, nothing is left running
    # each service isolates its faults (RNF-14): one task per service, like the workers
    tasks = [
        asyncio.create_task(work(), name=name)
        for name, work in engine.services() + engine.watchers()
    ]
    log.info("daemon running in %s", engine.mode)
    if on_ready is not None:
        on_ready()
    try:
        await stop.wait()
    finally:
        log.info("daemon: stopping")
        if control is not None:
            await control.aclose()  # attached TUIs see it leave
        if engine.bot is not None:
            engine.bot.disarm()
        for task in tasks:
            task.cancel()  # sends in flight are protected (D-29)
        await asyncio.gather(*tasks, return_exceptions=True)
        _open_orders_note(engine)
        await engine.aclose()
        log.info("daemon stopped")


def _open_orders_note(engine: Engine) -> None:
    if engine.mode != "live" or engine.account is None:
        return
    n = sum(len(st.orders) for st in engine.account.state.values())
    if n:
        what = "1 orden abierta" if n == 1 else f"{n} órdenes abiertas"
        text = f"daemon stopped con {what} en el exchange: siguen ahí (no se cancelan)"
        log.warning("%s", text)
        _record(engine, text)


def _record(engine: Engine, text: str) -> None:
    if engine.journal is None:
        return
    try:
        engine.journal.write("notice", data={"text": text})
    except (OSError, ValueError) as exc:  # disk full or journal closed: the log remains
        log.error("notice not written to the journal: %s", exc)
