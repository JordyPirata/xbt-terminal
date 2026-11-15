"""CustodyService: on-chain balance of the self-custody wallets (RF-38).

Scans at startup and every `every_s`: for each account key and branch (receive,
change), asks the chain's explorer address by address until `gap_limit` in a row
have no transactions, and adds up the balances per chain.

    wallets ─▶ derive address ─▶ Explorer.address ─▶ CustodyBalance(btc, btcb2, at)

A scan only counts if every query answered: a failed one keeps the previous balance,
is reported and retried on the next cycle, because a partial sum would look like
funds moved out. A balance older than three intervals stops counting (`balance()`).

Queries go one at a time, `request_every_ms` apart: mempool.space answered 429 after
about twenty in a row, and after thirty at one per second (2026-11-14). A 429 fails
the scan like any other error; there is no retry inside a scan (D-84).

Read-only and apart from the venues: it shares neither their rate limiter nor their
faults, and derives addresses in a thread so the bot's loop does not wait for it.

Spec: RF-37, RF-38, RNF-14, RNF-25, CA-22 · design.md §6.5 · D-84 · Task: T-103
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from functools import cache
from typing import Protocol

from ..adapters.explorer import AddressStats
from ..adapters.streaming import now_ms
from ..core.inventory import BASE, QUOTE, CustodyBalance
from ..core.models import ZERO
from ..core.wallet import CHANGE, HARDENED, RECEIVE, AccountKey, p2wpkh_address
from .faults import Faults

CUSTODY_EVERY_S = 3600  # self-custody balances change rarely; fewer queries (D-84)
GAP_LIMIT = 20
REQUEST_EVERY_MS = 3000  # public explorers answer 429 to queries close together (D-84)
MAX_AGE_INTERVALS = 3  # a balance older than this many intervals no longer counts


class _Explorer(Protocol):
    async def address(self, address: str) -> AddressStats: ...


@cache
def _branch_key(key: AccountKey, branch: int) -> AccountKey:
    return key.child(branch)


def derive(key: AccountKey, branch: int, index: int) -> str:
    """Native segwit address at `<key>/<branch>/<index>`; the branch key is derived once."""
    return p2wpkh_address(_branch_key(key, branch).child(index).public_key)


class CustodyService:
    def __init__(
        self,
        wallets: Mapping[str, Sequence[AccountKey]],
        explorers: Mapping[str, _Explorer],
        *,
        every_s: int = CUSTODY_EVERY_S,
        gap_limit: int = GAP_LIMIT,
        request_every_ms: int = REQUEST_EVERY_MS,
        pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], int] = now_ms,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        faults: Faults | None = None,
        derive: Callable[[AccountKey, int, int], str] = derive,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._wallets = wallets
        self._explorers = explorers
        self._every_s = every_s
        self._gap_limit = gap_limit
        self._request_every_s = request_every_ms / 1000
        self._pause = pause
        self._clock = clock
        self._sleep = sleep
        self._faults = faults
        self._derive = derive
        self._close = close
        self._addresses: dict[tuple[AccountKey, int, int], str] = {}
        self.last: CustodyBalance | None = None  # the last complete scan

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
            await self._sleep(self._every_s)

    async def scan(self) -> CustodyBalance:
        totals: dict[str, Decimal] = {}
        for asset, keys in self._wallets.items():
            explorer = self._explorers[asset]
            total = ZERO
            for key in keys:
                for branch in (RECEIVE, CHANGE):
                    total += await self._branch_balance(explorer, key, branch)
            totals[asset] = total
        self.last = CustodyBalance(
            totals.get(QUOTE, ZERO), totals.get(BASE, ZERO), self._clock()
        )
        return self.last

    async def _branch_balance(self, explorer: _Explorer, key: AccountKey, branch: int) -> Decimal:
        total = ZERO
        unused = 0
        index = 0
        while unused < self._gap_limit and index < HARDENED:
            address = await self._address(key, branch, index)
            if self._request_every_s:
                await self._pause(self._request_every_s)
            stats = await explorer.address(address)
            if stats.used:
                unused = 0
                total += stats.balance
            else:
                unused += 1
            index += 1
        return total

    async def _address(self, key: AccountKey, branch: int, index: int) -> str:
        where = (key, branch, index)
        if where not in self._addresses:
            # ~3 ms of pure Python each: off the loop the bot runs on
            self._addresses[where] = await asyncio.to_thread(self._derive, key, branch, index)
        return self._addresses[where]

    async def aclose(self) -> None:
        if self._close is not None:
            await self._close()
