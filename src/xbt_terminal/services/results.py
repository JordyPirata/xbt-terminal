"""Reads the journals for the results window (RF-25).

File reading, plus a single request: each venue's public fee token price (`pricers`,
D-65), to subtract in BTC the fees paid with it. Asked with `refresh_prices` (the
screen, on opening and on changing range), at most once a minute; `load` uses the
last that arrived and, without it, leaves those fees unvalued. For a range in days the
previous day is read too, so a double started before midnight has its sends and fills
are assigned as when reading everything (D-44, D-45).

Spec: RF-24, RF-25 · design.md §6.10 · Tasks: T-40, T-60
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from ..adapters.base import VenueError, describe
from ..adapters.streaming import now_ms
from ..core.results import DAY_MS, Range, Report, compute, range_start, read_rows

_NAME = re.compile(r"^journal-(\d{8})\.jsonl$")
PRICE_EVERY_MS = 60_000

Pricer = Callable[[], Awaitable[Decimal]]


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
        lines: list[str] = []
        for day, path in self._files():
            if first is None or day >= first:
                lines += path.read_text(encoding="utf-8").splitlines()
        rows, bad = read_rows(lines)
        return compute(rows, since_ms=since, bad_lines=bad, prices=self.prices)

    def _files(self) -> list[tuple[str, Path]]:
        if not self.directory.is_dir():
            return []
        found = []
        for path in self.directory.iterdir():
            match = _NAME.match(path.name)
            if match:
                found.append((match.group(1), path))
        return sorted(found)
