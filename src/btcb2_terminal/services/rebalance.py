"""Comisiones de retiro para el plan de reequilibrio (RF-29).

Solo lee: pide al venue la comisión de BTC y de BTCB2 cada vez que se carga,
sin caché ni disco (cambian, D-47); el fallo de un venue o activo queda como
texto en su clave y el plan lo marca no viable (D-53).

Spec: RF-29, CA-14 · design.md §6.12 · Tarea: T-45
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
        """Comisión o error por (venue, activo); las peticiones van en paralelo."""
        keys = [(vid, asset) for vid in self.venues for asset in ASSETS]
        got = await asyncio.gather(*(self._fee(vid, asset) for vid, asset in keys))
        return dict(zip(keys, got, strict=True))

    async def _fee(self, venue: VenueId, asset: str) -> FeeInfo:
        try:
            return await self._venues[venue].withdrawal_fee(asset)
        except (VenueError, httpx.HTTPError) as exc:
            return describe(exc)
