"""Diagnostic log: `<data>/diag.log`, rotating and apart from the journal.

The journal (RF-11) is the record of what was traded; this file is for understanding
what happened with the network: load errors, WS drops and reconnections, fallback
polling, account failures and bugs with their traceback. Each line goes whole
(message and traceback) through the `Redactor` and the file is created `0600`. It
never writes to stdout or stderr: the TUI owns the screen (RNF-19).

Modules use `logging.getLogger(__name__)`, all under `xbt_terminal`.

Spec: RNF-19, RNF-18 · design.md §8 · Task: T-31
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
    """Redact the formatted line: that covers message, arguments and traceback."""

    converter = time.gmtime  # UTC, like the journal

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
    """Diagnostic log handler. `close()` also removes it from the logger."""

    def _open(self) -> IO[Any]:
        # every file, new ones after rotating too, for the user only
        stream = open(  # closed by logging
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
    """Install the handler on the `xbt_terminal` logger. `data` already exists (`0700`).
    `name`: the attached TUI uses another file, to avoid rotating the daemon's (D-61)."""
    handler = DiagHandler(
        Path(data) / name, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(_RedactingFormatter(redactor))
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False  # nothing goes up to root, which could write on the screen
    return handler
