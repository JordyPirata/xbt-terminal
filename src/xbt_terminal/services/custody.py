"""CustodyService: on-chain balance of the watched addresses (RF-37, RF-38).

The user adds the addresses with the `watch` command; the list lives in
`<data>/watch.json`, written only by the daemon. At startup, every `every_s` and
right after the list changes, it asks the chain's explorer for each address and adds
the balances up per chain.

    watch.json ─▶ Explorer.address ─▶ CustodyBalance(btc, btcb2, at)

A scan only counts if every query answered: a failed one keeps the previous balance,
is reported and retried on the next cycle, because a partial sum would look like
funds moved out. A balance older than three intervals stops counting (`balance()`).
Changing the list drops the balance until a scan of the new list ends: the sum of
other addresses would be a wrong total (D-85).

The chains are scanned at the same time; within a chain, queries go one at a time,
`request_every_ms` apart: mempool.space answered 429 after about twenty in a row,
and after thirty at one per second (2026-11-14). A 429 fails the scan like any other
error; there is no retry inside a scan (D-84).

Read-only and apart from the venues: it shares neither their rate limiter nor their
faults. The addresses never reach the journal, the faults or the snapshot (RNF-25).

Spec: RF-37, RF-38, RNF-14, RNF-25, CA-22 · design.md §6.5 · D-85 · Tasks: T-103, T-108
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from ..adapters.explorer import AddressStats, ExplorerError
from ..adapters.streaming import now_ms
from ..core.address import AddressError, validate_address
from ..core.inventory import BASE, QUOTE, CustodyBalance
from ..core.models import ZERO
from .faults import Faults

CUSTODY_EVERY_S = 300  # the user wants the total fresh; the pause is after each scan (D-84)
REQUEST_EVERY_MS = 3000  # public explorers answer 429 to queries close together (D-84)
MAX_ADDRESSES = 50  # per chain: bounds the queries of a scan (D-85)
WATCH_FILE = "watch.json"
CHAIN_NAME = {QUOTE: "BTC", BASE: "XBT"}  # as the screen names them (D-48)
MAX_AGE_INTERVALS = 3  # a balance older than this many intervals no longer counts


class WatchError(ValueError):
    """The list cannot change that way; the message is fit for the status bar and never
    carries an address (RNF-25)."""


class _Explorer(Protocol):
    async def address(self, address: str) -> AddressStats: ...


class WatchList:
    """The addresses to watch per chain, kept in a `0600` JSON file (RNF-18, RNF-25)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._addresses: dict[str, list[str]] = {QUOTE: [], BASE: []}
        if path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for asset in self._addresses:
                self._addresses[asset] = [validate_address(text) for text in data.get(asset, [])]
        except (OSError, ValueError, AttributeError, TypeError):
            # the content may be addresses: say which file, not what is in it
            raise WatchError(f"{self._path.name}: ilegible o con direcciones no válidas") from None

    def addresses(self, asset: str) -> tuple[str, ...]:
        return tuple(self._addresses[asset])

    def any(self) -> bool:
        return any(self._addresses.values())

    def add(self, asset: str, text: str) -> None:
        try:
            address = validate_address(text)
        except AddressError as error:
            raise WatchError(str(error)) from None
        current = self._addresses[asset]
        if address in current:
            raise WatchError(f"ya se vigila en {CHAIN_NAME[asset]}")
        if len(current) >= MAX_ADDRESSES:
            raise WatchError(f"máximo {MAX_ADDRESSES} direcciones en {CHAIN_NAME[asset]}")
        self._save({**self._addresses, asset: [*current, address]})

    def remove(self, asset: str, text: str) -> None:
        try:
            address = validate_address(text)
        except AddressError as error:
            raise WatchError(str(error)) from None
        current = self._addresses[asset]
        if address not in current:
            raise WatchError(f"no se vigila en {CHAIN_NAME[asset]}")
        self._save({**self._addresses, asset: [kept for kept in current if kept != address]})

    def _save(self, addresses: dict[str, list[str]]) -> None:
        # written aside and renamed: a crash never leaves half a list
        temporary = self._path.with_name(self._path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(addresses, handle)
        os.replace(temporary, self._path)
        self._addresses = addresses


class CustodyService:
    def __init__(
        self,
        watchlist: WatchList,
        explorers: Mapping[str, _Explorer],
        *,
        every_s: int = CUSTODY_EVERY_S,
        request_every_ms: int = REQUEST_EVERY_MS,
        pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], int] = now_ms,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        faults: Faults | None = None,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._watchlist = watchlist
        self._explorers = explorers  # only the chains with a url in `[custody]`
        self._every_s = every_s
        self._request_every_s = request_every_ms / 1000
        self._pause = pause
        self._clock = clock
        self._sleep = sleep
        self._faults = faults
        self._close = close
        self._generation = 0  # bumped by each change: a scan of an older list is discarded
        self._changed = asyncio.Event()
        self.last: CustodyBalance | None = None  # the last complete scan

    @property
    def active(self) -> bool:
        """Whether there is any address to watch (what makes the total include them)."""
        return self._watchlist.any()

    def watched(self, asset: str) -> tuple[str, ...]:
        return self._watchlist.addresses(asset)

    def add(self, asset: str, address: str) -> None:
        self._require_explorer(asset)
        self._watchlist.add(asset, address)
        self._list_changed()

    def remove(self, asset: str, address: str) -> None:
        self._watchlist.remove(asset, address)
        self._list_changed()

    def _require_explorer(self, asset: str) -> None:
        if asset not in self._explorers:
            raise WatchError(f"falta la url de {CHAIN_NAME[asset]} en [custody]")

    def _list_changed(self) -> None:
        self._generation += 1
        self.last = None
        self._changed.set()

    def balance(self) -> CustodyBalance | None:
        """The last complete scan while it is fresh enough to count; else None."""
        if self.last is None:
            return None
        age_ms = self._clock() - self.last.at
        return self.last if age_ms <= MAX_AGE_INTERVALS * self._every_s * 1000 else None

    async def run(self) -> None:
        while True:
            try:
                await self.scan()
            except Exception as error:  # the explorer down does not stop the next scan
                if self._faults is None:
                    raise
                self._faults.report("custody", error)
            await self._rest()

    async def _rest(self) -> None:
        """Until the next scan: `every_s`, or sooner if the list changed meanwhile."""
        sleeper = asyncio.ensure_future(self._sleep(self._every_s))
        waker = asyncio.ensure_future(self._changed.wait())
        try:
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
            if sleeper.done():
                sleeper.result()
        finally:
            for pending in (sleeper, waker):
                pending.cancel()
            self._changed.clear()
            await asyncio.gather(sleeper, waker, return_exceptions=True)

    async def scan(self) -> CustodyBalance | None:
        generation = self._generation
        lists = {asset: self._watchlist.addresses(asset) for asset in (QUOTE, BASE)}
        assets = [asset for asset, addresses in lists.items() if addresses]
        if not assets:
            return None
        # chains at once: each has its own explorer, so its pause and limit are its own
        results = await asyncio.gather(
            *(self._chain_balance(asset, lists[asset]) for asset in assets),
            return_exceptions=True,
        )
        totals: dict[str, Decimal] = {}
        for asset, result in zip(assets, results, strict=True):
            if isinstance(result, BaseException):
                raise result
            totals[asset] = result
        if generation != self._generation:
            return None  # the list changed during the scan; the next one counts
        self.last = CustodyBalance(totals.get(QUOTE, ZERO), totals.get(BASE, ZERO), self._clock())
        return self.last

    async def _chain_balance(self, asset: str, addresses: tuple[str, ...]) -> Decimal:
        explorer = self._explorers[asset]
        total = ZERO
        for address in addresses:
            if self._request_every_s:
                await self._pause(self._request_every_s)
            try:
                total += (await explorer.address(address)).balance
            except ExplorerError as error:
                # which chain failed; still never the address (RNF-25)
                raise ExplorerError(f"{CHAIN_NAME[asset]}: {error}") from None
        return total

    async def aclose(self) -> None:
        if self._close is not None:
            await self._close()

