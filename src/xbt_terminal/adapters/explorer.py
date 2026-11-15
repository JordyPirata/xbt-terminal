"""Explorer client: on-chain balance of one address (RF-38).

Speaks the Esplora address endpoint, which mempool.space (BTC) and mempool.guide
(BTCB2) both serve (checked on 2026-11-14, docs/api/README.md):

    GET <url>/address/<address>  ->  {address, chain_stats, mempool_stats}

One address per request and nothing else: the xpub never leaves the machine
(RNF-25). Any answer that cannot be read is an error, never a zero balance, and
no error message carries the address, because derived addresses are not in the
`Redactor`.

Spec: RF-38, RNF-25, CA-22 · design.md §6.5 · D-84 · Task: T-102
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

SATS_PER_COIN = Decimal(100_000_000)
TIMEOUT_S = 10.0


class ExplorerError(Exception):
    """The explorer did not give a usable answer."""


@dataclass(frozen=True, slots=True)
class AddressStats:
    balance: Decimal  # coins, confirmed plus mempool
    used: bool  # has any transaction, confirmed or not


class Explorer:
    def __init__(self, http: httpx.AsyncClient, url: str) -> None:
        self._http = http
        self._url = url.rstrip("/")

    async def address(self, address: str) -> AddressStats:
        try:
            response = await self._http.get(f"{self._url}/address/{address}", timeout=TIMEOUT_S)
        except httpx.HTTPError as error:
            # `from None`: httpx errors carry the request, whose url has the address
            raise ExplorerError(f"sin respuesta del explorador ({type(error).__name__})") from None
        if response.status_code != 200:
            raise ExplorerError(f"el explorador respondió HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise ExplorerError("el explorador no respondió JSON") from None
        return parse_address(body, address)


def parse_address(body: Any, address: str) -> AddressStats:
    if not isinstance(body, dict) or body.get("address") != address:
        raise ExplorerError("respuesta del explorador con otra forma o de otra dirección")
    confirmed, confirmed_count = _net(body.get("chain_stats"))
    pending, pending_count = _net(body.get("mempool_stats"))
    balance = confirmed + pending
    if confirmed < 0 or balance < 0:
        raise ExplorerError("respuesta del explorador con saldo negativo")
    return AddressStats(Decimal(balance) / SATS_PER_COIN, confirmed_count + pending_count > 0)


def _net(stats: Any) -> tuple[int, int]:
    """(funded - spent in sats, transaction count) of one stats block."""
    if not isinstance(stats, dict):
        raise ExplorerError("respuesta del explorador sin estadísticas")
    funded, spent, count = (
        _count(stats.get(key)) for key in ("funded_txo_sum", "spent_txo_sum", "tx_count")
    )
    return funded - spent, count


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExplorerError("respuesta del explorador con un número ilegible")
    return value
