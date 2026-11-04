"""State of a double order and hanging leg (T-18, design.md §6.4, PA-08, D-15).

Hanging leg: the filled quantities of both legs differ and the one behind is rejected
or cancelled, or has gone more than `hanging_leg_after_s` without catching up."""

from dataclasses import replace

import pytest

from xbt_terminal.core.double import DoubleState, Leg, hanging, on_order, start
from xbt_terminal.core.models import D, Order, OrderRequest

T0 = 1_792_497_600_000
AFTER_MS = 30_000


def double(**kw) -> DoubleState:
    buy = OrderRequest("nk", "buy", D("0.01"), D("0.0072"), "t-b")
    sell = OrderRequest("nx", "sell", D("0.01"), D("0.0073"), "t-s")
    state = start("d-1", buy, sell, T0)
    return replace(state, **kw)


def sent(state, buy="o-b", sell="o-s") -> DoubleState:
    """Send responses: they bring each leg's client_id."""
    state = on_order(state, order("nk", buy, "buy", cid="t-b"), T0)
    return on_order(state, order("nx", sell, "sell", cid="t-s"), T0)


def order(venue, oid, side, filled="0", status="open", cid=None):
    return Order(venue, oid, cid, side, D("0.0072"), D("0.01"), D(filled), status, T0)


@pytest.mark.spec("RF-07")
class TestLegs:
    def test_both_pending_at_start(self):
        s = double()
        assert (s.buy.status, s.sell.status) == ("pending", "pending")
        assert (s.buy.order_id, s.sell.order_id) == (None, None)

    def test_send_reply_assigns_order_by_client_id(self):
        s = on_order(double(), order("nk", "o-b", "buy", cid="t-b"), T0)
        assert (s.buy.order_id, s.buy.status) == ("o-b", "open")
        assert s.sell.order_id is None

    def test_updates_go_by_order_id(self):
        s = sent(double())
        s = on_order(s, order("nx", "o-s", "sell", filled="0.004", status="partial"), T0 + 5)
        assert (s.sell.status, s.sell.filled) == ("partial", D("0.004"))
        assert s.buy.filled == 0

    def test_foreign_order_changes_nothing(self):
        s = sent(double())
        assert on_order(s, order("nk", "otra", "buy", filled="0.01", status="filled"), T0) == s

    def test_same_id_on_another_venue_does_not_count(self):
        s = sent(double())
        assert on_order(s, order("nx", "o-b", "sell", filled="0.01", status="filled"), T0) == s

    def test_failure_sending(self):
        s = double().fail("sell", "PRICE_OUT_OF_BAND")
        assert (s.sell.status, s.sell.error) == ("rejected", "PRICE_OUT_OF_BAND")

    def test_finished_when_both_finished(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.01", status="filled"), T0)
        assert not s.done
        s = on_order(s, order("nx", "o-s", "sell", filled="0.01", status="filled"), T0)
        assert s.done and hanging(s, T0 + 999_999, AFTER_MS) is None


@pytest.mark.spec("RF-07", "CA-03")
class TestHanging:
    def test_no_fills_no_imbalance(self):
        s = sent(double())
        assert s.imbalance_since is None
        assert hanging(s, T0 + 10 * AFTER_MS, AFTER_MS) is None

    def test_one_filled_other_open_over_n_seconds(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.01", status="filled"), T0 + 1_000)
        assert s.imbalance_since == T0 + 1_000
        assert hanging(s, T0 + 1_000 + AFTER_MS, AFTER_MS) is None  # not yet at the exact limit
        reason = hanging(s, T0 + 1_001 + AFTER_MS, AFTER_MS)
        assert reason is not None and "Neoxa" in reason and "30 s" in reason

    def test_partial_counts_too(self):
        s = sent(double())
        s = on_order(s, order("nx", "o-s", "sell", filled="0.002", status="partial"), T0)
        assert hanging(s, T0 + AFTER_MS + 1, AFTER_MS) is not None

    def test_equalizing_resets_the_clock(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.005", status="partial"), T0)
        s = on_order(s, order("nx", "o-s", "sell", filled="0.005", status="partial"), T0 + 20_000)
        assert s.imbalance_since is None
        s = on_order(s, order("nk", "o-b", "buy", filled="0.01", status="filled"), T0 + 25_000)
        assert s.imbalance_since == T0 + 25_000
        assert hanging(s, T0 + 25_000 + AFTER_MS, AFTER_MS) is None

    def test_more_fills_without_equalizing_keep_the_clock(self):
        """A leg filling in pieces without the other following keeps hanging."""
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.002", status="partial"), T0)
        s = on_order(s, order("nk", "o-b", "buy", filled="0.006", status="partial"), T0 + 20_000)
        assert s.imbalance_since == T0
        assert hanging(s, T0 + AFTER_MS + 1, AFTER_MS) is not None

    def test_one_filled_other_rejected_is_immediate(self):
        s = on_order(
            double(), order("nk", "o-b", "buy", filled="0.01", status="filled", cid="t-b"), T0
        )
        s = s.fail("sell", "Insufficient funds")
        reason = hanging(s, T0, AFTER_MS)
        assert reason is not None and "rechazada" in reason

    def test_one_partial_other_cancelled(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.003", status="partial"), T0)
        s = on_order(s, order("nx", "o-s", "sell", status="cancelled"), T0 + 1)
        assert "cancelada" in (hanging(s, T0 + 1, AFTER_MS) or "")

    def test_rejected_without_fills_on_other_is_not_hanging(self):
        """PA-08: a leg with fills is needed."""
        s = on_order(double(), order("nk", "o-b", "buy", cid="t-b"), T0).fail("sell", "x")
        assert hanging(s, T0 + 10 * AFTER_MS, AFTER_MS) is None

    def test_the_one_behind_is_hanging(self):
        s = sent(double())
        s = on_order(s, order("nk", "o-b", "buy", filled="0.004", status="partial"), T0)
        s = on_order(s, order("nx", "o-s", "sell", filled="0.01", status="filled"), T0)
        reason = hanging(s, T0 + AFTER_MS + 1, AFTER_MS)
        assert reason is not None and reason.startswith("compra en NonKYC")


def test_leg_is_immutable():
    leg = Leg(OrderRequest("nk", "buy", D("1"), D("1"), "t"))
    with pytest.raises(AttributeError):
        leg.status = "open"  # type: ignore[misc]


@pytest.mark.spec("RF-07", "RF-20", "RF-21")
class TestFirstLeg:
    """Bot double with `lead_venue` (D-75): the other leg waits unsent."""

    def test_other_waits_unsent(self):
        buy = OrderRequest("nk", "buy", D("0.01"), D("0.0072"), "t-b")
        sell = OrderRequest("nx", "sell", D("0.01"), D("0.0073"), "t-s")
        s = start("d-1", buy, sell, T0, held="buy")
        assert (s.buy.status, s.sell.status) == ("held", "pending")
        assert not s.buy.done and not s.done

    def test_unsent_counts_as_closed_with_zero(self):
        s = double().skip("buy")
        s = on_order(s, order("nx", "o-s", "sell", status="cancelled", cid="t-s"), T0)
        assert s.buy.status == "skipped" and s.buy.done and s.done
        assert hanging(s, T0, AFTER_MS) is None

    def test_unsent_with_other_filled_is_hanging(self):
        s = double().skip("buy")
        s = on_order(s, order("nx", "o-s", "sell", "0.004", "cancelled", cid="t-s"), T0)
        assert hanging(s, T0, AFTER_MS) == "compra en NonKYC sin enviar con 0.004 XBT sin cubrir"

    def test_sent_for_another_quantity(self):
        s = start(
            "d-1",
            OrderRequest("nk", "buy", D("0.01"), D("0.0072"), "t-b"),
            OrderRequest("nx", "sell", D("0.01"), D("0.0073"), "t-s"),
            T0,
            held="buy",
        )
        s = s.resize("buy", D("0.004"))
        assert s.buy.req.qty == D("0.004") and s.buy.status == "pending"
        assert s.buy.req.client_id == "t-b"
