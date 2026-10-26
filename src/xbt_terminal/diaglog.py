"""Log de diagnóstico: `<datos>/diag.log`, rotativo y separado del journal.

El journal (RF-11) es el registro de lo operado; este fichero es para entender
qué pasó con la red: errores de carga, caídas y reconexiones del WS, polling de
respaldo, fallos de la cuenta y bugs con su traceback. Cada línea pasa entera
(mensaje y traceback) por el `Redactor` y el fichero se crea `0600`. Nunca se
escribe en stdout ni stderr: la TUI ocupa la pantalla (RNF-19).

Los módulos usan `logging.getLogger(__name__)`, todos bajo `xbt_terminal`.

Spec: RNF-19, RNF-18 · design.md §8 · Tarea: T-31
"""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

from .core.secrets import Redactor

LOGGER = "xbt_terminal"
LOG_FILE = "diag.log"
MAX_BYTES = 1_000_000
BACKUPS = 5


class _RedactingFormatter(logging.Formatter):
    """Redacta la línea ya formateada: así cubre mensaje, argumentos y traceback."""

    converter = time.gmtime  # UTC, como el journal

    def __init__(self, redactor: Redactor) -> None:
        super().__init__(
            "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.redact(super().format(record))


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


class DiagHandler(RotatingFileHandler):
    """Handler del log de diagnóstico. `close()` además lo quita del logger."""

    def _open(self) -> IO[Any]:
        # cada fichero, también los nuevos tras rotar, solo para el usuario
        stream = open(  # lo cierra logging
            self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors,
            opener=_private,
        )  # fmt: skip
        os.fchmod(stream.fileno(), 0o600)
        return stream

    def close(self) -> None:
        logger = logging.getLogger(LOGGER)
        if self in logger.handlers:
            logger.removeHandler(self)
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
        super().close()


def setup_diagnostics(
    data: Path,
    redactor: Redactor,
    level: str,
    *,
    max_bytes: int = MAX_BYTES,
    backups: int = BACKUPS,
    name: str = LOG_FILE,
) -> DiagHandler:
    """Instala el handler en el logger `xbt_terminal`. `data` ya existe (`0700`).
    `name`: la TUI conectada usa otro fichero, para no rotar el del daemon (D-61)."""
    handler = DiagHandler(
        Path(data) / name, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(_RedactingFormatter(redactor))
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False  # nada sube al root, que podría escribir en la pantalla
    return handler
