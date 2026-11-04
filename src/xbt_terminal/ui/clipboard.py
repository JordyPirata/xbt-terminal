"""Copy to the clipboard with the system tool.

Textual's `App.copy_to_clipboard` only sends OSC 52, and GNOME Terminal (VTE) silently
ignores it: the address did not reach the clipboard (D-52). Here `wl-copy` (Wayland),
`xclip` and `xsel` (X11) are tried, those in the `PATH` with a graphical session, in
that order; the first to succeed wins.

All three stay in the background serving the selection: their output goes to
`/dev/null`, because with a pipe the wait would last as long as that process.

Spec: RF-28 · design.md §6.11 · Tasks: T-44
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
    """Copy `text` and return the name of the tool that did, or `None` if there is none
    or all fail."""
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
