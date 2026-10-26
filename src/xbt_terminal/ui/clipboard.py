"""Copiar al portapapeles con la herramienta del sistema.

`App.copy_to_clipboard` de Textual solo manda OSC 52, y GNOME Terminal (VTE) lo
ignora sin avisar: la dirección no llegaba al portapapeles (D-52). Aquí se
prueban `wl-copy` (Wayland), `xclip` y `xsel` (X11), las que estén en el `PATH`
y tengan sesión gráfica, en ese orden; la primera que sale bien gana.

Las tres se quedan en segundo plano sirviendo la selección: su salida va a
`/dev/null`, porque con un pipe la espera duraría lo que viva ese proceso.

Spec: RF-28 · design.md §6.11 · Tareas: T-44
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable, Mapping

TIMEOUT_S = 2.0

Run = Callable[[list[str], bytes], Awaitable[int]]
Clipboard = Callable[[str], Awaitable[str | None]]


def _candidates(env: Mapping[str, str]) -> list[list[str]]:
    out: list[list[str]] = []
    if env.get("WAYLAND_DISPLAY"):
        out.append(["wl-copy"])
    if env.get("DISPLAY"):
        out += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    return out


async def _run(argv: list[str], data: bytes) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.communicate(data), TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        raise
    return proc.returncode if proc.returncode is not None else 1


async def system_copy(
    text: str,
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Run = _run,
) -> str | None:
    """Copia `text` y devuelve el nombre de la herramienta que lo hizo, o `None`
    si no hay ninguna o todas fallan."""
    for argv in _candidates(os.environ if env is None else env):
        path = which(argv[0])
        if path is None:
            continue
        try:
            if await run([path, *argv[1:]], text.encode()) == 0:
                return argv[0]
        except (OSError, TimeoutError):
            continue
    return None
