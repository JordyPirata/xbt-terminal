"""Reads the journals for the results window (RF-25).

File reading, plus a single request: each venue's public fee token price (`pricers`,
D-65), to subtract in BTC the fees paid with it. Asked with `refresh_prices` (the
screen, on opening and on changing range), at most once a minute; `load` uses the
last that arrived and, without it, leaves those fees unvalued. For a range in days the
previous day is read too, so a double started before midnight has its sends and fills
are assigned as when reading everything (D-44, D-45).

Journal rows are kept per file (T-94): a file whose size and `mtime` did not change is
not read again, and one that only grew is read from where the last read stopped. The
journal is only appended to, so that is all that changes; any other change (smaller, or
the same size and newer) reads it whole. Only whole lines are kept: a half-written last
line is parsed on each load, as before, until it ends.

Spec: RF-24, RF-25, RF-32 · design.md §6.10 · Tasks: T-40, T-60, T-94
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from ..adapters.base import VenueError, describe
from ..adapters.streaming import now_ms
from ..core.results import DAY_MS, Range, Report, compute, range_start, read_rows

_NAME = re.compile(r"^journal-(\d{8})\.jsonl$")
PRICE_EVERY_MS = 60_000

Pricer = Callable[[], Awaitable[Decimal]]


@dataclass
class _Cached:
    """What was read of one journal: its whole lines, up to `offset` bytes, and what
    follows the last of them."""

    size: int = 0
    mtime_ns: int = 0
    offset: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    bad: int = 0
    tail: bytes = b""  # a line still being written: read again on the next change


class ResultsService:
    def __init__(
        self,
        directory: Path,
        clock: Callable[[], int] = now_ms,
        pricers: Mapping[str, Pricer] | None = None,
    ) -> None:
        """`pricers`: per fee token, how to ask its BTC price (D-65)."""
        self.directory = Path(directory)
        self._clock = clock
        self._pricers = dict(pricers or {})
        self.prices: dict[str, Decimal] = {}
        self.price_errors: dict[str, str] = {}
        self._priced_at: int | None = None
        self._cache: dict[Path, _Cached] = {}
        # the UI and the daemon load from worker threads, possibly two at a time
        self._lock = threading.Lock()

    async def refresh_prices(self) -> None:
        """Ask each token's price if the last is a minute old or more. A failure is noted in
        `price_errors` and keeps the previous price."""
        now = self._clock()
        fresh = self._priced_at is not None and now - self._priced_at < PRICE_EVERY_MS
        if not self._pricers or fresh:
            return
        self._priced_at = now
        assets = list(self._pricers)
        got = await asyncio.gather(*(self._pricers[a]() for a in assets), return_exceptions=True)
        for asset, price in zip(assets, got, strict=True):
            if isinstance(price, (VenueError, httpx.HTTPError)):
                self.price_errors[asset] = describe(price)
            elif isinstance(price, BaseException):
                raise price
            else:
                self.prices[asset] = price
                self.price_errors.pop(asset, None)

    async def refresh(self, rng: Range) -> str | None:
        """What the window asks before redrawing: here, the token price (the journal is read
        in `load`). The same face as `RemoteResults` (D-66)."""
        await self.refresh_prices()
        return None

    def load(self, rng: Range) -> Report:
        since = range_start(rng, self._clock())
        first = None
        if since is not None:
            first = datetime.fromtimestamp((since - DAY_MS) / 1000, UTC).strftime("%Y%m%d")
        rows: list[dict[str, Any]] = []
        bad = 0
        with self._lock:
            files = self._files()
            for gone in self._cache.keys() - {path for _, path in files}:
                del self._cache[gone]
            for day, path in files:
                if first is None or day >= first:
                    got = self._rows(path)
                    rows += got.rows
                    bad += got.bad
                    if got.tail.strip():
                        tail_rows, tail_bad = read_rows(_lines(got.tail))
                        rows += tail_rows
                        bad += tail_bad
        return compute(rows, since_ms=since, bad_lines=bad, prices=self.prices)

    def _rows(self, path: Path) -> _Cached:
        stat = path.stat()
        got = self._cache.get(path)
        if got is not None and (stat.st_size, stat.st_mtime_ns) == (got.size, got.mtime_ns):
            return got
        if got is None or stat.st_size <= got.size:
            got = _Cached()  # new, or not just appended to: read whole
        data = self._read_from(path, got.offset)
        whole, newline, got.tail = data.rpartition(b"\n")
        if newline:
            rows, bad = read_rows(_lines(whole))
            got.rows += rows
            got.bad += bad
            got.offset += len(whole) + 1
        else:
            got.tail = data
        got.size, got.mtime_ns = stat.st_size, stat.st_mtime_ns
        self._cache[path] = got
        return got

    @staticmethod
    def _read_from(path: Path, offset: int) -> bytes:
        with path.open("rb") as source:
            source.seek(offset)
            return source.read()

    def _files(self) -> list[tuple[str, Path]]:
        if not self.directory.is_dir():
            return []
        found = []
        for path in self.directory.iterdir():
            match = _NAME.match(path.name)
            if match:
                found.append((match.group(1), path))
        return sorted(found)


def _lines(data: bytes) -> list[str]:
    """As `read_text().splitlines()` did: universal newlines, so `\\r\\n` is one break."""
    return data.decode("utf-8").splitlines()
