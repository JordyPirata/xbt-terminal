"""Rutas de config, `.env` y datos; permisos e instancia única.

Por defecto siguen XDG y no dependen del directorio de arranque (RNF-20):

    config  $XDG_CONFIG_HOME/btcb2t/config.toml   (~/.config/btcb2t)
    .env    $XDG_CONFIG_HOME/btcb2t/.env
    datos   $XDG_STATE_HOME/btcb2t                (~/.local/state/btcb2t)

Los flags las cambian; una ruta relativa se resuelve contra el cwd al arrancar
y queda absoluta. El directorio de datos se crea `0700` y guarda el journal y el
lock de instancia única (RNF-17, RNF-18).

Spec: RNF-17, RNF-18, RNF-20 · design.md §8 · Tarea: T-30
"""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_DIR = "btcb2t"
LOCK_FILE = "btcb2t.lock"


@dataclass(frozen=True, slots=True)
class Paths:
    config: Path
    env: Path
    data: Path


def _xdg(environ: Mapping[str, str], var: str, fallback: str) -> Path:
    # XDG: una ruta relativa en la variable se ignora
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
    """Crea el directorio solo para el usuario; si ya existía, lo cierra a `0700`."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def env_permission_problem(path: Path) -> str | None:
    """Qué falla en los permisos del `.env`, o None. Sin fichero no hay problema."""
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


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    """`flock` exclusivo sobre `<datos>/btcb2t.lock`, mientras corre la terminal.

    El kernel lo suelta si el proceso muere, así que un lock huérfano no bloquea.
    El fichero guarda el pid, solo para el mensaje de error; no se borra al salir
    (borrarlo dejaría a otra instancia con un lock sobre un fichero ya sin nombre).
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
            raise AlreadyRunning(
                f"Ya hay un daemon corriendo con los datos en {self.data} "
                f"(pid {pid}). Ciérralo o arranca con otro --data-dir."
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # cerrar suelta el flock
            self._fd = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
