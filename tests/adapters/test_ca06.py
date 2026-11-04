"""CA-06: after a network cut, the connection recovers by itself.

A real local WS server goes down entirely (reconnects are refused) and returns on
the same port. While down, the stream keeps giving the book over REST; on return, it
ends up in `ws` again. NonKYC's real frames are used."""

import asyncio
import socket

import httpx
import pytest
from websockets.asyncio.server import serve

from xbt_terminal.adapters.nonkyc import NonKYCVenue
from xbt_terminal.core.models import BookSnapshot, ConnState, D

from .test_nonkyc_stream import FakeNonKYC, FreeLimiter, rest  # noqa: F401  (fixture)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Consumer:
    """Read the stream in the background and let tests wait for a condition."""

    def __init__(self, stream):
        self.stream = stream
        self.events: list = []
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        async for ev in self.stream:
            self.events.append(ev)

    async def wait(self, predicate, timeout=3.0):
        async with asyncio.timeout(timeout):
            while not predicate(self.events):
                await asyncio.sleep(0.005)

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.stream.aclose()


def last_state(events):
    states = [e for e in events if isinstance(e, ConnState)]
    return (states[-1].source, states[-1].status) if states else None


@pytest.mark.spec("CA-06", "RF-01", "RF-14")
async def test_ws_drops_and_returns(rest):  # noqa: F811
    port = free_port()
    async with httpx.AsyncClient() as http:
        venue = NonKYCVenue(
            "BTCB2/BTC",
            FreeLimiter(),
            maker_fee=D("0.002"),
            taker_fee=D("0.002"),
            http=http,
            ws_url=f"ws://127.0.0.1:{port}",
            fallback_every_s=0.01,
            backoff_base_s=0.01,
            backoff_max_s=0.05,
            ping_interval_s=0.05,
            latency_every_s=0.05,
        )
        server = await serve(FakeNonKYC("stay", "stay").handler, "127.0.0.1", port)
        consumer = Consumer(venue.market_stream())
        try:
            # 1. Connected by WS, with real latency from websockets pings.
            await consumer.wait(lambda evs: last_state(evs) == ("ws", "up"))
            await consumer.wait(
                lambda evs: any(
                    isinstance(e, ConnState) and e.source == "ws" and e.latency_ms for e in evs
                )
            )

            # 2. Total cut: the server vanishes and reconnects are refused.
            server.close()
            await server.wait_closed()
            await consumer.wait(lambda evs: last_state(evs) == ("rest", "up"))
            cut = len(consumer.events)
            await consumer.wait(
                lambda evs: any(isinstance(e, BookSnapshot) for e in evs[cut:])
            )  # still a book, now over REST

            # 3. The network returns: it reconnects by itself and ends up in ws again.
            server = await serve(FakeNonKYC("stay").handler, "127.0.0.1", port)
            await consumer.wait(lambda evs: last_state(evs) == ("ws", "up"))
            back = len(consumer.events)
            calls = len(rest.calls)
            await asyncio.sleep(0.1)
            assert len(rest.calls) == calls  # polling stopped
            assert all(
                not (isinstance(e, ConnState) and e.source == "rest")
                for e in consumer.events[back:]
            )
            ws_up = [e for e in consumer.events[back:] if isinstance(e, ConnState)]
            assert ws_up and all(e.source == "ws" and e.status == "up" for e in ws_up)
        finally:
            await consumer.close()
            server.close()
            await server.wait_closed()
