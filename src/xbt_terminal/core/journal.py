"""Append-only JSONL journal.

One line per event in `<data>/journal-YYYYMMDD.jsonl` (UTC date; `~/.local/state/xbt`
by default), flushed after each line: if the terminal dies right after sending an
order, the `order_sent` line is already on disk.

Secrets are masked on the values *before* encoding to JSON, not on the final line:
that way a secret with characters JSON escapes (`"`, `\\`) does not slip past the
Redactor.

Spec: RF-11 · design.md §7 · Task: T-04
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

from .secrets import Redactor, Secret

EVENTS = frozenset(
    {
        "order_sent",
        "order_ack",
        "order_error",
        "order_unknown",
        "reject_local",
        "cancel_sent",
        "cancel_ack",
        "fill",
        "fill_fee",  # corrected fee of an already written fill (D-64)
        "hanging_leg",
        "conn",
        "error",
        "bot_state",  # phase 2 (RF-23)
        "bot_fire",
        "bot_miss",  # the first leg did not fill (D-75)
        "notice",  # daemon notices (RF-30, D-61)
        "value",  # account value (RF-32, D-69)
        "book_resync",  # local book differs from REST (RNF-24, D-76)
    }
)


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_ms(ts: datetime) -> str:
    ts = ts.astimezone(UTC)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


class Journal:
    def __init__(
        self,
        directory: Path,
        mode: str,
        redactor: Redactor,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.directory = Path(directory)
        self.mode = mode
        self.redactor = redactor
        self.clock = clock
        self._file: IO[str] | None = None
        self._day: str | None = None

    def write(
        self,
        event: str,
        *,
        venue: str | None = None,
        client_id: str | None = None,
        data: Mapping[str, Any],
    ) -> None:
        if event not in EVENTS:
            raise ValueError(f"evento de journal desconocido: {event!r}")
        now = self.clock()
        record = {
            "ts": _iso_ms(now),
            "mode": self.mode,
            "venue": venue,
            "event": event,
            "client_id": client_id,
            "data": data,
        }
        # encoded whole before touching the file: an error leaves no half line
        line = json.dumps(self._plain(record), ensure_ascii=False, separators=(",", ":"))
        out = self._open_for(now)
        out.write(line + "\n")
        out.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file, self._day = None, None

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *error: object) -> None:
        self.close()

    def _open_for(self, now: datetime) -> IO[str]:
        day = now.astimezone(UTC).strftime("%Y%m%d")
        if self._file is None or day != self._day:
            self.close()
            # only the user reads the journal (RNF-18): directory 0700, file 0600
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            path = self.directory / f"journal-{day}.jsonl"
            out = open(path, "a", encoding="utf-8", opener=_private)
            os.fchmod(out.fileno(), 0o600)  # one already created with wider permissions
            self._file, self._day = out, day
        return self._file

    def _plain(self, value: Any) -> Any:
        """Convert to JSON types, masking every string, keys included."""
        if isinstance(value, Secret):
            return str(value)
        if isinstance(value, str):
            return self.redactor.redact(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, Decimal):
            return format(value, "f")  # str() would give "6E-8" for a 6 sat fee
        if isinstance(value, datetime):
            return _iso_ms(value)
        if isinstance(value, Mapping):
            return {self._plain(str(k)): self._plain(v) for k, v in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self._plain(v) for v in value]
        if is_dataclass(value) and not isinstance(value, type):
            return {f.name: self._plain(getattr(value, f.name)) for f in fields(value)}
        raise TypeError(f"no se puede escribir en el journal: {type(value).__name__}")
