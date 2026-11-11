"""What the contract battery asks of a venue under test.

The battery knows nothing about HTTP or WS: each venue has a `Harness` that serves
its fixtures and stages the situations a clause needs (a rejected send, a new own
trade). Adding a venue means writing one of these, not touching the checks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from xbt_terminal.core.models import D, OrderRequest

# Same placed order for everyone, so the fixtures can answer it
ORDER_ID = "t-abc123"


class SpyLimiter:
    def __init__(self) -> None:
        self.kinds: list[str] = []

    async def acquire(self, kind: str) -> None:
        self.kinds.append(kind)


class Harness:
    name: str
    implements: tuple[type, ...] = ()  # optional protocols the adapter claims
    secrets: tuple[str, ...] = ()
    token: str = "NKYC"  # fee token, for `TokenPricer`
    locked_btc: Any = D("0.00150005")  # BTC committed to orders in the balances fixture
    foreign_orders: tuple[str, ...] = ()  # in the active list, not to be reported
    gone_order: str  # active order that later leaves the list and has a known end

    venue: Any  # with keys
    read_only: Any  # without keys
    limiter: SpyLimiter

    @property
    def order(self) -> OrderRequest:
        """What the `place_limit` fixture answers."""
        return OrderRequest(self.venue.id, "buy", D("0.01"), D("0.00725"), ORDER_ID)

    @property
    def known(self) -> OrderRequest:
        """An order already on the venue, as an uncertain send would look for it."""
        return OrderRequest(self.venue.id, "buy", D("0.2"), D("0.0071"), ORDER_ID)

    async def __aenter__(self) -> Harness:
        raise NotImplementedError

    async def __aexit__(self, *error: object) -> None:
        raise NotImplementedError

    @asynccontextmanager
    async def stream_venue(self, *, drop: bool = False) -> AsyncIterator[Any]:
        """A venue with a live stream. `drop`: the first session ends after its data and
        the second one replays the same data."""
        raise NotImplementedError
        yield

    def dead_stream_venue(self) -> Any:
        """A venue whose stream cannot connect, while REST still answers."""
        raise NotImplementedError

    def garble_market(self) -> None:
        """`market()` receives valid JSON that is not a market."""
        raise NotImplementedError

    def venue_clock(self) -> int:
        """The time the venue under test believes it is, in ms."""
        raise NotImplementedError

    def date_market(self, ms: int) -> None:
        """`market()` receives a `Date` header saying `ms`."""
        raise NotImplementedError

    def send_outcome(self, outcome: str) -> None:
        """Next `place_limit`: rejected, refused, server_error, timeout, not_json or
        unreadable."""
        raise NotImplementedError

    def sent_text(self) -> str:
        """The last order as it travelled."""
        raise NotImplementedError

    def cancelled_text(self) -> str:
        """The last cancel as it travelled, id included."""
        raise NotImplementedError

    def fail_cancel(self) -> None:
        raise NotImplementedError

    def cancel_all_reached_venue(self) -> bool:
        raise NotImplementedError

    def new_own_trade(self) -> str:
        """The next refresh brings a new own trade; returns its id."""
        raise NotImplementedError

    def orders_gone(self) -> None:
        """The next refresh brings no active orders."""
        raise NotImplementedError

    def blank_deposit(self) -> None:
        """Deposit addresses arrive empty."""
        raise NotImplementedError

    def requests(self) -> int:
        """Requests that reached the venue so far."""
        raise NotImplementedError
