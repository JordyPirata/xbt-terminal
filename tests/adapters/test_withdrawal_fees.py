"""Withdrawal fees of the adapters (T-45, RF-29, D-53). Responses shaped like the real
ones of 2026-10-23, trimmed."""

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
    for fee in fees:
        fee.update(override.get(fee["token"], {}))
    return {"success": True, "fees": [fee for fee in fees if fee.get("token")]}


@pytest.mark.spec("RF-29", "CA-14")
class TestNonKYC:
    def test_fulfils_the_protocol(self, http):  # noqa: F811
        assert isinstance(nonkyc(http), WithdrawalFees)

    async def test_btc_is_main_network(self, http):  # noqa: F811
        limiter = Limiter()
        with respx.mock() as mock:
            route = mock.get(f"{NK_URL}/asset/getbyticker/BTC-MAIN").respond(
                json=nk_asset("BTC-MAIN", "0.000050", "0.00001")
            )
            got = await nonkyc(http, limiter).withdrawal_fee("BTC")
        assert got == WithdrawalFee("nk", "BTC", "BTC-MAIN", D("0.00005"), D("0.00001"), True)
        assert got.on_top is False  # assumption: deducted (D-53)
        assert "X-API-KEY" not in route.calls.last.request.headers  # it is public
        assert limiter.kinds == ["market"]

    async def test_btcb2_without_minimum(self, http):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTCB2").respond(json=nk_asset("BTCB2", "0.0027"))
            got = await nonkyc(http).withdrawal_fee("BTCB2")
        assert (got.fee, got.minimum, got.active) == (D("0.0027"), None, True)

    @pytest.mark.parametrize(("active", "maintenance"), [(False, False), (True, True)])
    async def test_closed_or_under_maintenance(self, http, active, maintenance):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTCB2").respond(
                json=nk_asset("BTCB2", "0.0027", active=active, maintenance=maintenance)
            )
            got = await nonkyc(http).withdrawal_fee("BTCB2")
        assert got.active is False

    async def test_other_ticker_is_error(self, http):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTC-MAIN").respond(json=nk_asset("BTC", "0.0008"))
            with pytest.raises(VenueError, match="BTC-MAIN"):
                await nonkyc(http).withdrawal_fee("BTC")

    async def test_missing_fee_is_error(self, http):  # noqa: F811
        payload = nk_asset("BTCB2", "")
        with respx.mock() as mock:
            mock.get(f"{NK_URL}/asset/getbyticker/BTCB2").respond(json=payload)
            with pytest.raises(VenueError, match="comisión"):
                await nonkyc(http).withdrawal_fee("BTCB2")

    async def test_asset_outside_the_pair(self, http):  # noqa: F811
        with pytest.raises(VenueError, match="USDT"):
            await nonkyc(http).withdrawal_fee("USDT")


@pytest.mark.spec("RF-29", "CA-14")
class TestNeoxa:
    def test_fulfils_the_protocol(self, http):  # noqa: F811
        assert isinstance(neoxa(http), WithdrawalFees)

    @pytest.mark.parametrize(
        ("asset", "chain", "minimum"), [("BTC", "bitcoin", "0.0005"), ("BTCB2", "btcb2", "0.01")]
    )
    async def test_by_chain(self, http, asset, chain, minimum):  # noqa: F811
        limiter = Limiter()
        with respx.mock() as mock:
            route = mock.get(f"{NX_URL}/withdrawals/fees").respond(json=nx_fees())
            got = await neoxa(http, limiter).withdrawal_fee(asset)
        # the fee is on top of the amount (D-53)
        assert got == WithdrawalFee("nx", asset, chain, D("0.0001"), D(minimum), True, True)
        assert route.calls.last.request.headers["X-API-Key"] == "fake-key"
        assert limiter.kinds == ["private"]

    async def test_numbers_instead_of_text(self, http):  # noqa: F811
        payload = nx_fees(BTCB2={"withdrawalFee": 1, "minWithdrawal": 10})
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/withdrawals/fees").respond(json=payload)
            got = await neoxa(http).withdrawal_fee("BTCB2")
        assert (got.fee, got.minimum) == (D("1"), D("10"))

    async def test_entry_missing(self, http):  # noqa: F811
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/withdrawals/fees").respond(json=nx_fees(BTC={"token": None}))
            with pytest.raises(VenueError, match="bitcoin"):
                await neoxa(http).withdrawal_fee("BTC")

    async def test_another_chain_does_not_count(self, http):  # noqa: F811
        payload = nx_fees(BTC={"chain": "bsc"})
        with respx.mock() as mock:
            mock.get(f"{NX_URL}/withdrawals/fees").respond(json=payload)
            with pytest.raises(VenueError):
                await neoxa(http).withdrawal_fee("BTC")

    async def test_no_keys(self, http):  # noqa: F811
        with respx.mock() as mock:
            with pytest.raises(ReadOnlyError):
                await neoxa(http, creds=None).withdrawal_fee("BTC")
            assert not mock.calls
