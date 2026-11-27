"""Diagnostic log, apart from the journal: the daemon's on stderr, the screen's in a file.

The journal (RF-11) is the record of what was traded; this log is for understanding
what happened with the network: load errors, WS drops and reconnections, fallback
polling, account failures and bugs with their traceback. Each line goes whole
(message and traceback) through the `Redactor`.

The daemon has no screen and runs under systemd, so it writes to stderr and journald
keeps and rotates it (D-89). The screen owns the terminal, so it writes to
`<data>/client.log`, rotating and `0600`, and never to stdout or stderr (RNF-19).

Modules use `logging.getLogger(__name__)`, all under `xbt_terminal`.

Spec: RNF-19, RNF-18 · design.md §8 · Task: T-31, T-112
"""

from __future__ import annotations

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

from .core.secrets import Redactor

LOGGER = "xbt_terminal"
CLIENT_LOG = "client.log"
MAX_BYTES = 1_000_000
BACKUPS = 5

# sd-daemon(3) priorities: journald reads them from the `<N>` line prefix
_PRIORITY = {
    logging.DEBUG: 7,
    logging.INFO: 6,
    logging.WARNING: 4,
    logging.ERROR: 3,
    logging.CRITICAL: 2,
}


# logback's level names, padded like its `%-5level`
_LEVEL = {"WARNING": "WARN", "CRITICAL": "FATAL"}


class _RedactingFormatter(logging.Formatter):
    """logback-style line (`date time.ms LEVEL logger - message`), redacted whole:
    that covers message, arguments and traceback."""

    converter = time.gmtime  # UTC, like the journal

    def __init__(self, redactor: Redactor) -> None:
        super().__init__(
            "%(asctime)s.%(msecs)03dZ %(level)-5s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        record.level = _LEVEL.get(record.levelname, record.levelname)
        return self._redactor.redact(super().format(record))


class _JournaldFormatter(_RedactingFormatter):
    """journald reads the `<N>` prefix as the line's priority, so `-p` filters."""

    def format(self, record: logging.LogRecord) -> str:
        return f"<{_PRIORITY.get(record.levelno, 6)}>{super().format(record)}"


class _Detaching(logging.Handler):
    """`close()` also removes the handler from the logger and gives it back to root."""

    def close(self) -> None:
        logger = logging.getLogger(LOGGER)
        if self in logger.handlers:
            logger.removeHandler(self)
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
        super().close()


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


class DiagHandler(_Detaching, RotatingFileHandler):
    """The screen's log file."""

    def _open(self) -> IO[Any]:
        # every file, new ones after rotating too, for the user only
        stream = open(  # closed by logging
            self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors,
            opener=_private,
        )  # fmt: skip
        os.fchmod(stream.fileno(), 0o600)
        return stream


class DaemonHandler(_Detaching, logging.StreamHandler):
    """The daemon's log on stderr."""


def _install(handler: logging.Handler, level: str) -> None:
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False  # nothing goes up to root, which could write on the screen


def setup_diagnostics(
    data: Path,
    redactor: Redactor,
    level: str,
    *,
    max_bytes: int = MAX_BYTES,
    backups: int = BACKUPS,
    name: str = CLIENT_LOG,
) -> DiagHandler:
    """Install the screen's file handler on `xbt_terminal`. `data` already exists (`0700`)."""
    handler = DiagHandler(
        Path(data) / name, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(_RedactingFormatter(redactor))
    _install(handler, level)
    return handler


def setup_daemon_diagnostics(redactor: Redactor, level: str) -> DaemonHandler:
    """Install the daemon's stderr handler on `xbt_terminal`. systemd sets
    `JOURNAL_STREAM` when stderr goes to journald (D-89)."""
    handler = DaemonHandler(sys.stderr)
    journald = "JOURNAL_STREAM" in os.environ
    handler.setFormatter((_JournaldFormatter if journald else _RedactingFormatter)(redactor))
    _install(handler, level)
    return handler
