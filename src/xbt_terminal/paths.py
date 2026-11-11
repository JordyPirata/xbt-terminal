"""Paths of config, `.env` and data; permissions and single instance.

By default they follow XDG and do not depend on the startup directory (RNF-20):

    config  $XDG_CONFIG_HOME/xbt/config.toml   (~/.config/xbt)
    .env    $XDG_CONFIG_HOME/xbt/.env
    data    $XDG_STATE_HOME/xbt                (~/.local/state/xbt)

Flags change them; a relative path resolves against the cwd at startup and stays
absolute. The data directory is created `0700` and holds the journal and the single
instance lock (RNF-17, RNF-18).

Spec: RNF-17, RNF-18, RNF-20 · design.md §8 · Task: T-30
"""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_DIR = "xbt"
LOCK_FILE = "xbt.lock"


@dataclass(frozen=True, slots=True)
class Paths:
    config: Path
    env: Path
    data: Path


def _xdg(environ: Mapping[str, str], var: str, fallback: str) -> Path:
    # XDG: a relative path in the variable is ignored
    value = environ.get(var, "")
    if value and Path(value).is_absolute():
        return Path(value)
    home = environ.get("HOME")
    return (Path(home) if home else Path.home()) / fallback


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().absolute()


def resolve_paths(
    config: str | Path | None = None,
    env: str | Path | None = None,
    data_dir: str | Path | None = None,
    environ: Mapping[str, str] = os.environ,
) -> Paths:
    conf_dir = _xdg(environ, "XDG_CONFIG_HOME", ".config") / APP_DIR
    data = _xdg(environ, "XDG_STATE_HOME", ".local/state") / APP_DIR
    return Paths(
        config=_absolute(config if config is not None else conf_dir / "config.toml"),
        env=_absolute(env if env is not None else conf_dir / ".env"),
        data=_absolute(data_dir if data_dir is not None else data),
    )


def private_dir(path: Path) -> None:
    """Create the directory for the user only; if it existed, tighten it to `0700`."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def env_permission_problem(path: Path) -> str | None:
    """What is wrong with `.env` permissions, or None. No file, no problem."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None
    if mode & 0o077 == 0:
        return None
    return (
        f"{path} tiene permisos {mode:04o}: otros usuarios pueden leer tus keys. "
        f"Arréglalo con: chmod 600 {path}"
    )


class AlreadyRunningError(Exception):
    pass


class InstanceLock:
    """Exclusive `flock` on `<data>/xbt.lock`, while the terminal runs.

    The kernel releases it if the process dies, so an orphan lock blocks nothing. The
    file keeps the pid, just for the error message; it is not deleted on exit (deleting
    it would leave another instance with a lock on a nameless file).
    """

    def __init__(self, data: Path) -> None:
        self.data = data
        self.path = data / LOCK_FILE
        self._fd: int | None = None

    def acquire(self) -> None:
        private_dir(self.data)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid = os.read(fd, 32).decode(errors="replace").strip() or "?"
            os.close(fd)
            raise AlreadyRunningError(
                f"Ya hay un daemon corriendo con los datos en {self.data} "
                f"(pid {pid}). Ciérralo o arranca con otro --data-dir."
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing releases the flock
            self._fd = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *error: object) -> None:
        self.release()
