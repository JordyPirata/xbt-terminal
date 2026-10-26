"""Qué apuntan en el log de diagnóstico el supervisor, el hub, la cuenta y los
fallos (T-31, RNF-19)."""

import logging

import pytest

from xbt_terminal.services.account import AccountService
from xbt_terminal.services.faults import Faults

from ..adapters.test_supervisor import WS_BOOK, FakeVenue, count, run
from ..fakes import FakeFeed
from .test_market_hub import Hub


def messages(caplog, level=logging.DEBUG):
    return [r.getMessage() for r in caplog.records if r.levelno >= level]


@pytest.mark.spec("RNF-19")
async def test_reconexion_del_ws(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    venue = FakeVenue([WS_BOOK, "drop"], [WS_BOOK])
    await run(venue, lambda evs: count(evs, ("ws", "up")) == 2)
    text = "\n".join(messages(caplog))
    assert "Fake: WS caído" in text and "caída" in text and "reintento en" in text
    assert "Fake: WS conectado" in text


@pytest.mark.spec("RNF-19")
async def test_respaldo_rest(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    venue = FakeVenue("fail", "fail", "fail", [WS_BOOK])
    await run(venue, lambda evs: count(evs, ("ws", "up")) == 1)
    text = "\n".join(messages(caplog))
    assert "Fake: polling REST de respaldo" in text
    assert "conexión rechazada" in text


@pytest.mark.spec("RNF-19")
async def test_carga_del_mercado(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    feeds = {"nk": FakeFeed("nk", fail_market=1), "nx": FakeFeed("nx")}
    async with Hub(**feeds) as h:
        await h.until(lambda: h.hub.state["nk"].ready and h.hub.state["nx"].ready)
    warnings = messages(caplog, logging.WARNING)
    assert any("nk: no se pudo cargar el mercado" in m and "sin red" in m for m in warnings)
    assert sum("mercado cargado" in m for m in messages(caplog)) == 2


@pytest.mark.spec("RNF-19")
async def test_cada_fallo_con_traceback(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    try:
        raise KeyError("bug")
    except KeyError as exc:
        Faults().report("market", exc, venue="nx")
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "fallo en market (nx)" in record.getMessage() and record.exc_info is not None


@pytest.mark.spec("RNF-19")
async def test_errores_de_la_cuenta_una_vez_por_racha(caplog):
    caplog.set_level(logging.INFO, logger="xbt_terminal")
    account = AccountService({})  # sin journal: el log de diagnóstico apunta igual
    for _ in range(3):
        account._journal_error("nx", "balances", "Neoxa: 503")
    account._journal_error("nx", "balances", "Neoxa: 502")
    warnings = messages(caplog, logging.WARNING)
    assert warnings == ["nx: balances: Neoxa: 503", "nx: balances: Neoxa: 502"]
