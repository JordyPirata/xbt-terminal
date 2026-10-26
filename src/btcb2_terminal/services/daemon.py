"""Daemon: el motor sin pantalla, en primer plano, hasta que le piden parar.

No sabe nada del gestor de servicios que lo lance (D-61): quien lo supervise
le manda SIGTERM o SIGINT (`__main__` los convierte en `stop`). Al parar,
desarma el bot, espera a los envíos y cancelaciones en vuelo (RNF-15) y, en
live, apunta si quedan órdenes abiertas: no hay a quién preguntar. Los avisos
que la terminal mostraría van al log de diagnóstico, y los de error también
al journal como `notice`.

Spec: RF-30, RNF-15 · design.md §6.13 · Tarea: T-51 · D-61
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
    """Con `socket`, abre el de control para el cliente `btcb2t` (RF-31); el book
    viaja con `book_depth` niveles por lado (D-66)."""

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
        await control.start()  # antes que las tareas: si falla, no queda nada corriendo
    # cada servicio aísla sus fallos (RNF-14): una tarea por servicio, como los workers
    tasks = [
        asyncio.create_task(work(), name=name)
        for name, work in engine.services() + engine.watchers()
    ]
    log.info("daemon en marcha en %s", engine.mode)
    if on_ready is not None:
        on_ready()
    try:
        await stop.wait()
    finally:
        log.info("daemon: parando")
        if control is not None:
            await control.aclose()  # las TUI conectadas ven que se fue
        if engine.bot is not None:
            engine.bot.disarm()
        for task in tasks:
            task.cancel()  # los envíos en vuelo van protegidos (D-29)
        await asyncio.gather(*tasks, return_exceptions=True)
        _open_orders_note(engine)
        await engine.aclose()
        log.info("daemon parado")


def _open_orders_note(engine: Engine) -> None:
    if engine.mode != "live" or engine.account is None:
        return
    n = sum(len(st.orders) for st in engine.account.state.values())
    if n:
        what = "1 orden abierta" if n == 1 else f"{n} órdenes abiertas"
        text = f"daemon parado con {what} en el exchange: siguen ahí (no se cancelan)"
        log.warning("%s", text)
        _record(engine, text)


def _record(engine: Engine, text: str) -> None:
    if engine.journal is None:
        return
    try:
        engine.journal.write("notice", data={"text": text})
    except (OSError, ValueError) as exc:  # disco lleno o journal cerrado: queda el log
        log.error("aviso sin escribir en el journal: %s", exc)
