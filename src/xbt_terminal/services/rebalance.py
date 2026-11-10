"""Withdrawal fees for the rebalance plan (RF-29).

Read only: asks the venue for the BTC and BTCB2 fee on each load, with no cache or
disk (they change, D-47); a venue or asset failure stays as text under its key and
the plan marks it not viable (D-53).

Spec: RF-29, CA-14 · design.md §6.12 · Task: T-45
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx

from ..adapters.base import VenueError, WithdrawalFees, describe
from ..core.models import VenueId
from ..core.rebalance import FeeInfo

ASSETS = ("BTC", "BTCB2")


class RebalanceService:
    def __init__(self, venues: Mapping[VenueId, WithdrawalFees]) -> None:
        self._venues = dict(venues)
        self.venues: tuple[VenueId, ...] = tuple(self._venues)

    async def load(self) -> dict[tuple[VenueId, str], FeeInfo]:
        """Fee or error by (venue, asset); requests go in parallel."""
        keys = [(venue_id, asset) for venue_id in self.venues for asset in ASSETS]
        got = await asyncio.gather(*(self._fee(venue_id, asset) for venue_id, asset in keys))
        return dict(zip(keys, got, strict=True))

    async def _fee(self, venue: VenueId, asset: str) -> FeeInfo:
        try:
            return await self._venues[venue].withdrawal_fee(asset)
        except (VenueError, httpx.HTTPError) as error:
            return describe(error)
