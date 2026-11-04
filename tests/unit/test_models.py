from decimal import Decimal

import pytest

from xbt_terminal.core.models import (
    Balance,
    D,
    Level,
    MarketSpec,
    Order,
    OrderRequest,
)


def spec(tick="0.01", lot="0.0001", **kw) -> MarketSpec:
    fields = dict(
        venue="nk",
        symbol="BTCB2/USDT",
        base="BTCB2",
        quote="USDT",
        tick_size=D(tick),
        lot_size=D(lot),
        min_qty=D("0.0001"),
        min_notional=D("1"),
        maker_fee=D("0.002"),
        taker_fee=D("0.002"),
    )
    fields.update(kw)
    return MarketSpec(**fields)


@pytest.mark.spec("RNF-07")
class TestQuantizePrice:
    def test_buy_rounds_up_to_tick(self):
        assert spec().quantize_price(D("850.001"), "buy") == D("850.01")

    def test_sell_rounds_down_to_tick(self):
        assert spec().quantize_price(D("850.019"), "sell") == D("850.01")

    def test_price_on_tick_unchanged(self):
        assert spec().quantize_price(D("850.01"), "buy") == D("850.01")
        assert spec().quantize_price(D("850.01"), "sell") == D("850.01")

    def test_tick_not_power_of_ten(self):
        m = spec(tick="0.05")
        assert m.quantize_price(D("850.01"), "buy") == D("850.05")
        assert m.quantize_price(D("850.04"), "sell") == D("850.00")

    def test_result_with_tick_exponent(self):
        # 850 -> "850.00", not "8.5000E+2": what goes to the venue is str(price)
        assert str(spec().quantize_price(D("850"), "buy")) == "850.00"

    def test_invalid_side(self):
        with pytest.raises(ValueError):
            spec().quantize_price(D("850"), "long")


@pytest.mark.spec("RNF-07")
class TestQuantizeQty:
    def test_truncates_to_lot(self):
        assert spec().quantize_qty(D("0.012399")) == D("0.0123")

    def test_never_rounds_up(self):
        assert spec(lot="0.01").quantize_qty(D("0.0199")) == D("0.01")

    def test_below_lot_is_zero(self):
        assert spec().quantize_qty(D("0.00009")) == D("0")


@pytest.mark.spec("RNF-07")
class TestDecimalOnly:
    def test_D_rejects_float(self):  # noqa: N802
        with pytest.raises(TypeError):
            D(0.1)

    def test_D_accepts_str_and_int(self):  # noqa: N802
        assert D("0.1") == Decimal("0.1")
        assert D(3) == Decimal(3)

    def test_marketspec_with_float_fails(self):
        with pytest.raises(TypeError):
            spec(tick_size=0.01)

    def test_level_with_float_fails(self):
        with pytest.raises(TypeError):
            Level(price=850.0, qty=D("1"))

    def test_order_with_float_fails(self):
        with pytest.raises(TypeError):
            OrderRequest(venue="nk", side="buy", qty=0.01, price=D("850"), client_id="t-1")

    def test_tick_and_lot_must_be_positive(self):
        with pytest.raises(ValueError):
            spec(tick="0")
        with pytest.raises(ValueError):
            spec(lot="-0.01")


def test_balance_total():
    assert Balance("USDT", D("10"), D("2.5")).total == D("12.5")


def test_order_remaining_and_finished():
    o = Order("nk", "id1", "t-1", "buy", D("850"), D("0.01"), D("0.004"), "partial", 0)
    assert o.remaining == D("0.006")
    assert not o.is_done
    assert Order("nk", "id1", "t-1", "buy", D("850"), D("0.01"), D("0.01"), "filled", 0).is_done
