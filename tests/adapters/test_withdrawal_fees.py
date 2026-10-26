"""Comisiones de retiro de los adaptadores (T-45, RF-29, D-53). Respuestas con la
forma de las reales del 2026-10-23, recortadas."""

import pytest
import respx

from xbt_terminal.adapters.base import ReadOnlyError, VenueError, WithdrawalFees
from xbt_terminal.adapters.neoxa import REST_URL as NX_URL
from xbt_terminal.adapters.nonkyc import REST_URL as NK_URL
from xbt_terminal.core.models import D, WithdrawalFee

from .test_deposits import Limiter, http, neoxa, nonkyc  # noqa: F401  (fixture)


def nk_asset(ticker, fee, minimum=None, active=True, maintenance=False):
    payload = {
        "ticker": ticker,
        "name": "Bitcoin",
        "isActive": True,
        "isMaintenance": maintenance,
        "withdrawalActive": active,
        "withdrawDecimals": 8,
        "withdrawFee": fee,
        "withdrawFeeOverride": 5e-05,
        "minimumDeposit": "0.00005",
    }
    if minimum is not None:
        payload["minimumWithdraw"] = minimum
    return payload


def nx_fees(**override):
    fees = [
        {"token": "BTC", "chain": "bitcoin", "withdrawalFee": "0.0001",
         "minWithdrawal": "0.0005", "autoLimit": "0.5"},
        {"token": "BTCB2", "chain": "btcb2", "withdrawalFee": "0.0001",
         "minWithdrawal": "0.01", "autoLimit": 200},
        {"token": "DOGE", "chain": "dogecoin", "withdrawalFee": 2, "minWithdrawal": 10},
    ]  # fmt: skip
    for f in fees:
        f.update(override.get(f["token"], {}))
    return {"success": True, "fees": [f for f in fees if f.get("token")]}


@pytest.mark.spec("RF-29", "CA-14")
class TestNonKYC:
    def test_cumple_el_protocolo(self, http):  # noqa: F811
        assert isinstance(nonkyc(http), WithdrawalFees)

    async def test_btc_es_el_de_la_red_principal(self, http):  # noqa: F811
        limiter = Limiter()
        with respx.mock() as mock:
            route = mock.get(f"{NK_URL}/asset/getbyticker/BTC-MAIN").respond(
                json=nk_asset("BTC-MAIN", "0.000050", "0.00001")
            )
            got = await nonkyc(http, limiter).withdrawal_fee("BTC")
        assert got == WithdrawalFee("nk", "BTC", "BTC-MAIN", D("0.00005"), D("0.00001"), True)
        assert got.on_top is False  # supuesto: descontada (D-53)
        assert "X-API-KEY" not in route.calls.last.request.headers  # es público
        assert limiter.kinds == ["market"]

    async def test_btcb2_sin_minimo(self, http):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTCB2").respond(json=nk_asset("BTCB2", "0.0027"))
            got = await nonkyc(http).withdrawal_fee("BTCB2")
        assert (got.fee, got.minimum, got.active) == (D("0.0027"), None, True)

    @pytest.mark.parametrize(("active", "maintenance"), [(False, False), (True, True)])
    async def test_cerrado_o_en_mantenimiento(self, http, active, maintenance):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTCB2").respond(
                json=nk_asset("BTCB2", "0.0027", active=active, maintenance=maintenance)
            )
            got = await nonkyc(http).withdrawal_fee("BTCB2")
        assert got.active is False

    async def test_otro_ticker_es_error(self, http):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTC-MAIN").respond(json=nk_asset("BTC", "0.0008"))
            with pytest.raises(VenueError, match="BTC-MAIN"):
                await nonkyc(http).withdrawal_fee("BTC")

    async def test_sin_comision_es_error(self, http):  # noqa: F811
        payload = nk_asset("BTCB2", "")
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTCB2").respond(json=payload)
            with pytest.raises(VenueError, match="comisión"):
                await nonkyc(http).withdrawal_fee("BTCB2")

    async def test_activo_fuera_del_par(self, http):  # noqa: F811
        with pytest.raises(VenueError, match="USDT"):
            await nonkyc(http).withdrawal_fee("USDT")


@pytest.mark.spec("RF-29", "CA-14")
class TestNeoxa:
    def test_cumple_el_protocolo(self, http):  # noqa: F811
        assert isinstance(neoxa(http), WithdrawalFees)

    @pytest.mark.parametrize(
        ("asset", "chain", "minimum"), [("BTC", "bitcoin", "0.0005"), ("BTCB2", "btcb2", "0.01")]
    )
    async def test_por_cadena(self, http, asset, chain, minimum):  # noqa: F811
        limiter = Limiter()
        with respx.mock() as mock:
            route = mock.get(f"{NX_URL}/withdrawals/fees").respond(json=nx_fees())
            got = await neoxa(http, limiter).withdrawal_fee(asset)
        # la comisión va aparte de lo pedido (D-53)
        assert got == WithdrawalFee("nx", asset, chain, D("0.0001"), D(minimum), True, True)
        assert route.calls.last.request.headers["X-API-Key"] == "fake-key"
        assert limiter.kinds == ["private"]

    async def test_numeros_en_vez_de_texto(self, http):  # noqa: F811
        payload = nx_fees(BTCB2={"withdrawalFee": 1, "minWithdrawal": 10})
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/withdrawals/fees").respond(json=payload)
            got = await neoxa(http).withdrawal_fee("BTCB2")
        assert (got.fee, got.minimum) == (D("1"), D("10"))

    async def test_falta_la_entrada(self, http):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/withdrawals/fees").respond(json=nx_fees(BTC={"token": None}))
            with pytest.raises(VenueError, match="bitcoin"):
                await neoxa(http).withdrawal_fee("BTC")

    async def test_otra_cadena_no_vale(self, http):  # noqa: F811
        payload = nx_fees(BTC={"chain": "bsc"})
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/withdrawals/fees").respond(json=payload)
            with pytest.raises(VenueError):
                await neoxa(http).withdrawal_fee("BTC")

    async def test_sin_keys(self, http):  # noqa: F811
        with respx.mock() as mock:
            with pytest.raises(ReadOnlyError):
                await neoxa(http, creds=None).withdrawal_fee("BTC")
            assert not mock.calls
