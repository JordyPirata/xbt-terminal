"""Market recording (RF-33, D-80): what the hub applies, to a file of its own.

First line `{"format":1}`; the others `{"t", "venue", "kind", "data"}`, with the local
arrival time and data as on the socket (`wire`). It records what the hub accepts, not
the raw stream: a replay sees what the bot saw. Public data only: the recorder does
not know the account.

A write failure stops the recording and is reported (RNF-14): the market goes on.

Spec: RF-33, RF-34, CA-19 · design.md §6.14 · Task: T-87
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from ..adapters.streaming import now_ms
from ..core.models import BookDelta, BookSnapshot, ConnState, MarketSpec, Trade, VenueId
from .market import MarketHub
from .wire import decode, dumps, loads

FORMAT = 1
VENUE_COUNT = 2  # a recording has both venues (RNF-09)
KINDS: dict[str, type] = {
    "market": MarketSpec,
    "book": BookSnapshot,
    "delta": BookDelta,
    "trade": Trade,
    "conn": ConnState,
}
_KIND_OF = {target_type: kind for kind, target_type in KINDS.items()}


class RecordingError(ValueError):
    """Unreadable recording; the message gives file and line."""


def recording_path(directory: Path, now: int) -> Path:
    stamp = datetime.fromtimestamp(now / 1000, UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(directory) / f"books-{stamp}.jsonl"


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


class Recorder:
    def __init__(self, hub: MarketHub, path: Path, *, clock: Callable[[], int] = now_ms) -> None:
        self._hub = hub
        self._clock = clock
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._file: IO[bytes] | None = open(self.path, "xb", opener=_private)
        self._write(dumps({"format": FORMAT}))
        hub.subscribe(self._on_change)
        hub.subscribe_events(self._on_event)

    def _on_change(self, venue: VenueId, what: str) -> None:
        spec = self._hub.state[venue].spec
        if what == "market" and spec is not None:
            self._line(venue, spec)

    def _on_event(self, venue: VenueId, event: object) -> None:
        self._line(venue, event)

    def _line(self, venue: VenueId, event: object) -> None:
        if self._file is None:
            return
        kind = _KIND_OF[type(event)]
        self._write(dumps({"t": self._clock(), "venue": venue, "kind": kind, "data": event}))

    def _write(self, line: bytes) -> None:
        assert self._file is not None
        try:
            self._file.write(line)
            self._file.flush()
        except OSError as error:
            self._file = None  # the recording ends here; the market goes on
            self._hub.faults.report("recording", error, note="grabación parada")

    def close(self) -> None:
        file, self._file = self._file, None
        if file is not None:
            try:
                file.close()
            except OSError as error:
                self._hub.faults.report("recording", error)


@dataclass(frozen=True, slots=True)
class Recorded:
    t: int
    venue: VenueId
    event: Any


def _row(path: Path, number: int, raw: bytes) -> Recorded:
    try:
        row = loads(raw)
        target_type = KINDS[row["kind"]]
        return Recorded(int(row["t"]), row["venue"], decode(target_type, row["data"]))
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        raise RecordingError(f"{path}:{number}: línea mala: {error!r}") from None


def read_recording(path: Path) -> Iterator[Recorded]:
    """Line by line: a long recording does not fit in memory."""
    try:
        with open(path, "rb") as source:
            first = source.readline()
            try:
                version = loads(first).get("format")
            except ValueError:
                version = None
            if version != FORMAT:
                raise RecordingError(f"{path}: formato de grabación desconocido")
            for number, raw in enumerate(source, 2):
                yield _row(path, number, raw)
    except OSError as error:
        raise RecordingError(f"{path}: no se puede leer: {error}") from None


@dataclass(frozen=True, slots=True)
class RecordingInfo:
    first: int  # time of the first event
    last: int
    specs: dict[VenueId, MarketSpec]


def scan_recording(path: Path) -> RecordingInfo:
    """Validate the whole file: better to fail at startup than halfway."""
    first = last = None
    specs: dict[VenueId, MarketSpec] = {}
    for row in read_recording(path):
        first = row.t if first is None else first
        last = row.t
        if isinstance(row.event, MarketSpec):
            specs[row.venue] = row.event
    missing = {"nk", "nx"} - set(specs)
    if first is None or last is None or len(specs) != VENUE_COUNT:
        raise RecordingError(f"{path}: falta el mercado de {', '.join(sorted(missing)) or 'nadie'}")
    return RecordingInfo(first, last, specs)
