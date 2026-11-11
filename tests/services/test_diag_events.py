"""What the supervisor, hub, account and faults note in the diagnostic log (T-31,
RNF-19)."""

import logging

import pytest

from xbt_terminal.services.account import AccountService
from xbt_terminal.services.faults import Faults

from ..adapters.test_supervisor import WS_BOOK, FakeVenue, count, run
from ..fakes import FakeFeed
from .test_market_hub import Hub


def messages(caplog, level=logging.DEBUG):
    return [record.getMessage() for record in caplog.records if record.levelno >= level]


@pytest.mark.spec("RNF-19")
async def test_ws_reconnection(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    venue = FakeVenue([WS_BOOK, "drop"], [WS_BOOK])
    await run(venue, lambda evs: count(evs, ("ws", "up")) == 2)
    text = "\n".join(messages(caplog))
    assert "Fake: WS down" in text and "caída" in text and "retrying in" in text
    assert "Fake: WS connected" in text


@pytest.mark.spec("RNF-19")
async def test_rest_fallback(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    venue = FakeVenue("fail", "fail", "fail", [WS_BOOK])
    await run(venue, lambda evs: count(evs, ("ws", "up")) == 1)
    text = "\n".join(messages(caplog))
    assert "Fake: fallback REST polling" in text
    assert "conexión rechazada" in text


@pytest.mark.spec("RNF-19")
async def test_market_load(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    feeds = {"nk": FakeFeed("nk", fail_market=1), "nx": FakeFeed("nx")}
    async with Hub(**feeds) as hub:
        await hub.until(lambda: hub.hub.state["nk"].ready and hub.hub.state["nx"].ready)
    warnings = messages(caplog, logging.WARNING)
    assert any(
        "nk: could not load the market" in message and "sin red" in message for message in warnings
    )
    assert sum("market loaded" in message for message in messages(caplog)) == 2


@pytest.mark.spec("RNF-19")
async def test_each_fault_with_traceback(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    try:
        raise KeyError("bug")
    except KeyError as error:
        Faults().report("market", error, venue="nx")
    (record,) = [entry for entry in caplog.records if entry.levelno == logging.ERROR]
    assert "fault in market (nx)" in record.getMessage() and record.exc_info is not None


@pytest.mark.spec("RNF-19")
async def test_account_errors_once_per_streak(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    account = AccountService({})  # no journal: the diagnostic log notes anyway
    for _ in range(3):
        account._journal_error("nx", "balances", "Neoxa: 503")
    account._journal_error("nx", "balances", "Neoxa: 502")
    warnings = messages(caplog, logging.WARNING)
    assert warnings == ["nx: balances: Neoxa: 503", "nx: balances: Neoxa: 502"]
