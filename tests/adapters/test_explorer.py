"""Explorer client (T-102, RF-38, CA-22, D-84). Fixtures recorded on 2026-11-14 from
mempool.guide (BTCB2) and mempool.space (BTC) with the BIP173 example address, which
belongs to nobody: never an own address in a fixture."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from xbt_terminal.adapters.explorer import AddressStats, Explorer, ExplorerError
from xbt_terminal.core.models import D

FIXTURES = Path(__file__).parent.parent / "fixtures" / "explorer"
URL = "https://explorer.test/api"
ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


def stats(funded=0, spent=0, count=0):
    return {
        "funded_txo_count": count,
        "funded_txo_sum": funded,
        "spent_txo_count": 0,
        "spent_txo_sum": spent,
        "tx_count": count,
    }


def payload(chain=None, mempool=None, address=ADDRESS):
    return {
        "address": address,
        "chain_stats": chain or stats(),
        "mempool_stats": mempool or stats(),
    }


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


async def ask(http, response=None, **respond):
    with respx.mock() as mock:
        route = mock.get(f"{URL}/address/{ADDRESS}")
        route.respond(json=response, **respond) if response is not None else route.respond(
            **respond
        )
        return await Explorer(http, URL).address(ADDRESS)


@pytest.mark.spec("RF-38", "CA-22")
class TestAddress:
    @pytest.mark.parametrize("name", ["address_btcb2.json", "address_btc.json"])
    async def test_recorded_response_of_a_spent_address(self, http, name):
        recorded = json.loads((FIXTURES / name).read_text())
        assert await ask(http, recorded) == AddressStats(D("0"), True)

    async def test_confirmed_balance_in_coins(self, http):
        got = await ask(http, payload(chain=stats(150_000_000, 25_000_000, 3)))
        assert got == AddressStats(D("1.25"), True)
        assert isinstance(got.balance, type(D("0")))

    async def test_mempool_counts(self, http):
        incoming = await ask(http, payload(mempool=stats(funded=5_000, count=1)))
        assert incoming == AddressStats(D("0.00005"), True)
        outgoing = await ask(
            http, payload(chain=stats(10_000, 0, 1), mempool=stats(spent=10_000, count=1))
        )
        assert outgoing == AddressStats(D("0"), True)

    async def test_unused_address(self, http):
        assert await ask(http, payload()) == AddressStats(D("0"), False)

    async def test_sends_its_own_user_agent(self, http):
        with respx.mock() as mock:
            route = mock.get(f"{URL}/address/{ADDRESS}").respond(json=payload())
            await Explorer(http, URL).address(ADDRESS)
        agent = route.calls.last.request.headers["User-Agent"]
        assert agent == "xbt-terminal" and "httpx" not in agent

    async def test_trailing_slash_in_the_url(self, http):
        with respx.mock() as mock:
            mock.get(f"{URL}/address/{ADDRESS}").respond(json=payload())
            assert (await Explorer(http, URL + "/").address(ADDRESS)).used is False


@pytest.mark.spec("RF-38", "RNF-25")
class TestFailures:
    @pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
    async def test_http_error_is_never_a_zero_balance(self, http, status):
        with pytest.raises(ExplorerError):
            await ask(http, status_code=status, text="Invalid Bitcoin address")

    async def test_not_json(self, http):
        with pytest.raises(ExplorerError):
            await ask(http, text="<html>busy</html>")

    @pytest.mark.parametrize(
        "broken",
        [
            [],
            {},
            {"address": ADDRESS, "chain_stats": stats()},
            payload(chain={"funded_txo_sum": 1}),
            payload(chain=stats(funded="100")),
            payload(chain=stats(funded=1.5)),
            payload(chain=stats(funded=True)),
            payload(chain=stats(funded=-1)),
            payload(chain=stats(funded=1, spent=2)),
            payload(address="bc1qsomeoneelse"),
        ],
    )
    async def test_unreadable_shape(self, http, broken):
        with pytest.raises(ExplorerError):
            await ask(http, broken)

    async def test_network_failure(self, http):
        with respx.mock() as mock:
            mock.get(f"{URL}/address/{ADDRESS}").mock(side_effect=httpx.ConnectError("down"))
            with pytest.raises(ExplorerError):
                await Explorer(http, URL).address(ADDRESS)

    async def test_error_never_carries_the_address(self, http):
        with respx.mock() as mock:
            mock.get(f"{URL}/address/{ADDRESS}").respond(status_code=500, text=ADDRESS)
            with pytest.raises(ExplorerError) as raised:
                await Explorer(http, URL).address(ADDRESS)
        chain = [raised.value, raised.value.__cause__, raised.value.__context__]
        assert all(ADDRESS not in str(error) for error in chain if error is not None)
