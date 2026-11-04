"""Deposit addresses for their window (RF-28).

Read only: asks the venue for the BTC and BTCB2 address on each load, with no cache
or disk, and a venue or asset failure stays in its row (D-50).

Spec: RF-28, CA-13 · design.md §6.11 · Task: T-42
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx

from ..adapters.base import Depositor, VenueError, describe
from ..core.models import DepositRow, VenueId

ASSETS = ("BTC", "BTCB2")




class DepositService:
    def __init__(self, venues: Mapping[VenueId, Depositor]) -> None:
        self._venues = dict(venues)
        self.venues: tuple[VenueId, ...] = tuple(self._venues)

    async def load(self) -> list[DepositRow]:
        """One row per venue and asset, in order; requests go in parallel."""
        pairs = [(vid, asset) for vid in self.venues for asset in ASSETS]
        return list(await asyncio.gather(*(self._row(vid, asset) for vid, asset in pairs)))

    async def _row(self, venue: VenueId, asset: str) -> DepositRow:
        try:
            address = await self._venues[venue].deposit_address(asset)
        except (VenueError, httpx.HTTPError) as exc:
            return DepositRow(venue, asset, None, describe(exc))
        return DepositRow(venue, asset, address)
