"""Lee los journals para la ventana de resultados (RF-25).

Lectura de ficheros, más una sola petición: el precio público del token de fee
de cada venue (`pricers`, D-65), para restar en BTC las fees pagadas con él. Se
pide con `refresh_prices` (la pantalla, al abrir y al cambiar de rango), a lo
sumo una vez por minuto; `load` usa el último que llegó y, sin él, deja esas
fees sin valorar. Para un rango por días se lee también el día anterior, para
que una doble empezada antes de medianoche tenga sus envíos y los fills se
asignen igual que leyendo todo (D-44, D-45).

Spec: RF-24, RF-25 · design.md §6.10 · Tareas: T-40, T-60
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
        """`pricers`: por token de fee, cómo pedir su precio en BTC (D-65)."""
        self.directory = Path(directory)
        self._clock = clock
        self._pricers = dict(pricers or {})
        self.prices: dict[str, Decimal] = {}
        self.price_errors: dict[str, str] = {}
        self._priced_at: int | None = None

    async def refresh_prices(self) -> None:
        """Pide el precio de cada token si el último tiene un minuto o más. Un
        fallo se apunta en `price_errors` y conserva el precio anterior."""
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
        """Lo que la ventana pide antes de redibujar: aquí, el precio del token
        (el journal se lee en `load`). La misma cara que `RemoteResults` (D-66)."""
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
