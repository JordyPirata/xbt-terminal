"""Direcciones de depósito de los adaptadores (T-42, RF-28, D-50). Respuestas con
la forma de las reales del 2026-10-22, con direcciones inventadas."""

import httpx
import pytest
import respx

from btcb2_terminal.adapters.base import Depositor, ReadOnlyError, VenueError
from btcb2_terminal.adapters.neoxa import REST_URL as NX_URL
from btcb2_terminal.adapters.neoxa import NeoxaVenue
from btcb2_terminal.adapters.nonkyc import REST_URL as NK_URL
from btcb2_terminal.adapters.nonkyc import NonKYCVenue
from btcb2_terminal.config import Credentials
from btcb2_terminal.core.models import D, DepositAddress
from btcb2_terminal.core.secrets import Secret

CREDS = Credentials(Secret("fake-key"), Secret("fake-secret"))
NK_BTC = "bc1qfakefakefakefakefakefakefakefakex9vz"
NK_BTCB2 = "bc1qfakeblake2bfakefakefakefakefakey6nf"
NX_BTC = "bc1qneoxafakefakefakefakefakefakefd3h3"
NX_BTCB2 = "1MejFakeFakeFakeFakeFakeFakeHEjk"


class Limiter:
    def __init__(self) -> None:
        self.kinds: list[str] = []

    async def acquire(self, kind: str) -> None:
        self.kinds.append(kind)


def nk_payload(ticker, address, network="Bitcoin Main Chain", minimum="0.00005", confs=2):
    return {
        "address": address,
        "paymentid": "",
        "ticker": ticker,
        "network": network,
        "minimumDeposit": minimum,
        "confirmsRequired": confs,
    }


def nx_payload(**addresses):
    base = {"litecoin": "ltc1qfake", "bitcoin": NX_BTC, "btcb2": NX_BTCB2}
    base.update(addresses)
    return {
        "success": True,
        "addresses": {k: v for k, v in base.items() if v is not None},
        "closedChains": {"dero": "DERO deposits are currently disabled."},
    }


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def nonkyc(http, limiter=None, creds=CREDS):
    return NonKYCVenue(
        "BTCB2/BTC", limiter or Limiter(), maker_fee=D("0.002"), taker_fee=D("0.002"),
        http=http, credentials=creds, clock=lambda: 1,
    )  # fmt: skip


def neoxa(http, limiter=None, creds=CREDS):
    return NeoxaVenue("BTCB2_BTC", limiter or Limiter(), http=http, credentials=creds)


@pytest.mark.spec("RF-28", "CA-13")
class TestNonKYC:
    def test_cumple_depositor(self, http):
        assert isinstance(nonkyc(http), Depositor)

    async def test_btc_por_la_red_principal(self, http):
        limiter = Limiter()
        with respx.mock() as mock:
            route = mock.get(f"{NK_URL}/getdepositaddress/BTC-MAIN").respond(
                json=nk_payload("BTC-MAIN", NK_BTC)
            )
            got = await nonkyc(http, limiter).deposit_address("BTC")
        assert got == DepositAddress(
            "nk", "BTC", "BTC-MAIN", "Bitcoin Main Chain", NK_BTC, "", D("0.00005"), 2
        )
        assert route.calls.last.request.headers["X-API-KEY"] == "fake-key"
        assert limiter.kinds == ["private"]

    async def test_btcb2(self, http):
        payload = nk_payload("BTCB2", NK_BTCB2, "XBT (BTCB2 / Blake2b fork)", "0.001", 15)
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/getdepositaddress/BTCB2").respond(json=payload)
            got = await nonkyc(http).deposit_address("BTCB2")
        assert (got.ticker, got.address, got.min_deposit, got.confirmations) == (
            "BTCB2", NK_BTCB2, D("0.001"), 15,
        )  # fmt: skip

    async def test_otro_ticker_en_la_respuesta_es_error(self, http):
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/getdepositaddress/BTCB2").respond(
                json=nk_payload("BTC-MAIN", NK_BTC)
            )
            with pytest.raises(VenueError, match="BTC-MAIN"):
                await nonkyc(http).deposit_address("BTCB2")

    async def test_direccion_vacia_es_error(self, http):
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/getdepositaddress/BTC-MAIN").respond(
                json=nk_payload("BTC-MAIN", "")
            )
            with pytest.raises(VenueError, match="sin dirección"):
                await nonkyc(http).deposit_address("BTC")

    async def test_error_del_venue(self, http):
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/getdepositaddress/BTC-MAIN").respond(
                400, json={"error": {"code": 20010, "message": "Must use more specific ticker"}}
            )
            with pytest.raises(VenueError, match="20010"):
                await nonkyc(http).deposit_address("BTC")

    async def test_sin_keys(self, http):
        with respx.mock() as mock:
            with pytest.raises(ReadOnlyError):
                await nonkyc(http, creds=None).deposit_address("BTC")
            assert not mock.calls

    async def test_activo_fuera_del_par(self, http):
        with pytest.raises(VenueError, match="USDT"):
            await nonkyc(http).deposit_address("USDT")


@pytest.mark.spec("RF-28", "CA-13")
class TestNeoxa:
    def test_cumple_depositor(self, http):
        assert isinstance(neoxa(http), Depositor)

    @pytest.mark.parametrize(
        ("asset", "chain", "address"), [("BTC", "bitcoin", NX_BTC), ("BTCB2", "btcb2", NX_BTCB2)]
    )
    async def test_por_cadena(self, http, asset, chain, address):
        limiter = Limiter()
        with respx.mock() as mock:
            route = mock.get(f"{NX_URL}/deposits/addresses").respond(json=nx_payload())
            got = await neoxa(http, limiter).deposit_address(asset)
        assert got == DepositAddress("nx", asset, chain, chain, address)
        assert route.calls.last.request.headers["X-API-Key"] == "fake-key"
        assert limiter.kinds == ["private"]

    async def test_cadena_cerrada(self, http):
        payload = nx_payload()
        payload["closedChains"]["btcb2"] = "BTCB2 deposits are currently disabled."
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/deposits/addresses").respond(json=payload)
            with pytest.raises(VenueError, match="disabled"):
                await neoxa(http).deposit_address("BTCB2")

    async def test_sin_la_cadena(self, http):
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/deposits/addresses").respond(json=nx_payload(bitcoin=None))
            with pytest.raises(VenueError, match="sin dirección"):
                await neoxa(http).deposit_address("BTC")

    async def test_direccion_vacia(self, http):
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/deposits/addresses").respond(json=nx_payload(btcb2=" "))
            with pytest.raises(VenueError, match="sin dirección"):
                await neoxa(http).deposit_address("BTCB2")

    async def test_sin_keys(self, http):
        with respx.mock() as mock:
            with pytest.raises(ReadOnlyError):
                await neoxa(http, creds=None).deposit_address("BTC")
            assert not mock.calls
