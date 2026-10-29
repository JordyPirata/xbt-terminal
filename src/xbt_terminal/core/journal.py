"""Journal append-only en JSONL.

Una línea por evento en `<datos>/journal-YYYYMMDD.jsonl` (fecha UTC; por
defecto, `~/.local/state/xbt`), con flush tras cada línea: si la terminal muere
justo después de enviar una orden, la línea `order_sent` ya está en disco.

Los secretos se enmascaran sobre los valores *antes* de codificar a JSON, no
sobre la línea final: así un secreto con caracteres que JSON escapa (`"`, `\\`)
tampoco se escapa del Redactor.

Spec: RF-11 · design.md §7 · Tarea: T-04
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
        "fill_fee",  # fee corregida de un fill ya apuntado (D-64)
        "hanging_leg",
        "conn",
        "error",
        "bot_state",  # fase 2 (RF-23)
        "bot_fire",
        "bot_miss",  # la primera pierna no llenó (D-75)
        "notice",  # avisos del daemon (RF-30, D-61)
        "value",  # valor de la cuenta (RF-32, D-69)
        "book_resync",  # book local distinto del REST (RNF-24, D-76)
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
        # se codifica entero antes de tocar el fichero: un error no deja media línea
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

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _open_for(self, now: datetime) -> IO[str]:
        day = now.astimezone(UTC).strftime("%Y%m%d")
        if self._file is None or day != self._day:
            self.close()
            # solo el usuario lee el journal (RNF-18): directorio 0700, fichero 0600
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            path = self.directory / f"journal-{day}.jsonl"
            out = open(path, "a", encoding="utf-8", opener=_private)
            os.fchmod(out.fileno(), 0o600)  # uno ya creado con más permisos
            self._file, self._day = out, day
        return self._file

    def _plain(self, value: Any) -> Any:
        """Convierte a tipos JSON enmascarando cada string, incluidas las claves."""
        if isinstance(value, Secret):
            return str(value)
        if isinstance(value, str):
            return self.redactor.redact(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, Decimal):
            return format(value, "f")  # str() daría "6E-8" en una fee de 6 sats
        if isinstance(value, datetime):
            return _iso_ms(value)
        if isinstance(value, Mapping):
            return {self._plain(str(k)): self._plain(v) for k, v in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self._plain(v) for v in value]
        if is_dataclass(value) and not isinstance(value, type):
            return {f.name: self._plain(getattr(value, f.name)) for f in fields(value)}
        raise TypeError(f"no se puede escribir en el journal: {type(value).__name__}")
