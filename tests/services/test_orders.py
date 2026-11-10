"""OrderService in dry-run (T-16, CA-04): validate, confirm, send and leave it all in
the journal. NonKYC book 0.00720000 / 0.00730000 → mid 0.00725; simulated balances
0.005 BTC and 0.5 BTCB2 (value ≈ 0.008625 BTC < cap 0.01)."""

import json
from dataclasses import replace

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.core.commands import Percent, PlaceOrder
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, BookSnapshot, ConnState, D, Level, Order
from xbt_terminal.core.risk import RiskLimits
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.market import MarketHub
from xbt_terminal.services.orders import Failed, OrderService, Pending, Rejected, Sent

from ..fakes import T0, FakeFeed, spec

NOW = T0 + 60_000
LIMITS = RiskLimits(
    max_order_pct=D("25"),
    max_mid_deviation_pct=D("5"),
    max_venue_exposure=D("0.01"),
    stale_after_s=15,
    hanging_leg_after_s=30,
)


class Setup:
    def __init__(self, tmp_path, *, btc="0.005", btcb2="0.5", limits=LIMITS, book_ts=NOW):
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.hub_now = NOW  # hub local time: the book's arrival time (RNF-16)
        self.hub = MarketHub(feeds, clock=lambda: self.hub_now)
        for venue_id in feeds:
            self.hub.state[venue_id].spec = spec(venue_id)
        self.book("nk", "0.00720000", "0.00730000", ts=book_ts)
        balances = {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D(btcb2))}
        self.venue = DryRunVenue(feeds["nk"], balances, clock=lambda: NOW)
        self.journal_dir = tmp_path / "logs"
        self.journal = Journal(self.journal_dir, "dry-run", Redactor([]))
        ids = iter(f"t-{i:06d}" for i in range(1, 100))
        self.orders = OrderService(
            self.hub,
            {"nk": self.venue},  # nx without keys: read only
            self.journal,
            limits,
            clock=lambda: NOW,
            new_client_id=lambda: next(ids),
        )

    def book(self, venue, bid, ask, ts=NOW, seq=None, received=None):
        """`ts` is the exchange time; received at `received` (by default, at `ts`)."""
        snap = BookSnapshot(venue, (Level(D(bid), D("1")),), (Level(D(ask), D("1")),), ts, seq)
        self.hub_now = ts if received is None else received
        self.hub.apply(venue, snap)
        self.hub_now = NOW

    def lines(self):
        files = sorted(self.journal_dir.glob("journal-*.jsonl"))
        return [json.loads(ln) for f in files for ln in f.read_text().splitlines()]


def buy(qty="0.01", price="0.00725000", venue="nk"):
    return PlaceOrder(venue, "buy", D(qty), D(price))


def sell(quantity="0.01", price="0.00725000", venue="nk"):
    return PlaceOrder(venue, "sell", D(quantity), D(price))


@pytest.mark.spec("RF-06", "RF-11", "RF-13")
class TestValidOrder:
    async def test_prepares_with_notional_and_fee(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())
        assert isinstance(p, Pending)
        assert p.req.client_id == "t-000001"
        assert p.notional == D("0.0000725") and p.fee == D("0.0000725") * D("0.002")
        assert "BUY 0.0100 XBT @ 0.00725000" in p.summary and "0.00007250 BTC" in p.summary
        assert "NonKYC" in p.summary
        assert s.lines() == []  # nothing to the journal until confirmed

    async def test_confirm_sends_and_records_sent_and_ack(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())
        result = await s.orders.confirm(p)
        assert isinstance(result, Sent) and result.order.order_id == "dry-nk-1"
        assert [o.order_id for o in await s.venue.open_orders()] == ["dry-nk-1"]
        sent, ack = s.lines()
        assert (sent["event"], ack["event"]) == ("order_sent", "order_ack")
        assert sent["client_id"] == ack["client_id"] == "t-000001"
        assert sent["mode"] == "dry-run" and sent["venue"] == "nk"
        assert sent["data"] == {"side": "buy", "qty": "0.01", "price": "0.00725000"}
        assert ack["data"] == {"order_id": "dry-nk-1", "status": "open"}

    async def test_confirm_validates_again(self, tmp_path):
        """The market may move with the dialog open: fail closed."""
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())
        s.book("nk", "0.00800000", "0.00810000")  # the mid moves 11 %
        result = await s.orders.confirm(p)
        assert isinstance(result, Rejected) and "mid_deviation" in result.codes
        assert await s.venue.open_orders() == []
        assert [ln["event"] for ln in s.lines()] == ["reject_local"]

    async def test_venue_error_stays_as_order_error(self, tmp_path):
        s = Setup(tmp_path)

        async def boom(order_request):
            raise VenueError("venue caído")

        s.venue.place_limit = boom
        result = await s.orders.confirm(await s.orders.prepare(buy()))
        assert isinstance(result, Failed) and "venue caído" in result.error
        assert [ln["event"] for ln in s.lines()] == ["order_sent", "order_error"]
        assert s.lines()[1]["data"] == {"error": "venue caído"}


def codes(result):
    assert isinstance(result, Rejected), result
    return set(result.codes)


@pytest.mark.spec("CA-04", "RF-10", "RF-11")
class TestRejections:
    """Each RF-10 rule rejects in dry-run, leaves `reject_local` and sends nothing."""

    async def check(self, s, command, code):
        result = await s.orders.prepare(command)
        assert code in codes(result)
        last = s.lines()[-1]
        assert last["event"] == "reject_local" and last["venue"] == command.venue
        assert code in {v["code"] for v in last["data"]["violations"]}
        assert last["data"]["price"] == str(command.price)
        return result

    async def test_order_size(self, tmp_path):
        """Inventory 0.005 + 0.5 × 0.00725 = 0.008625 BTC; 25 % = 0.00215625."""
        s = Setup(tmp_path)
        assert isinstance(await s.orders.prepare(buy(qty="0.2974")), Pending)  # 0.00215615
        await self.check(s, buy(qty="0.2975"), "order_size")  # 0.00215688

    async def test_order_size_adds_both_venues(self, tmp_path):
        """A venue without keys does not count; with both, the cap rises."""
        from xbt_terminal.adapters.dryrun import DryRunVenue

        s = Setup(tmp_path)
        s.book("nx", "0.00720000", "0.00730000")
        bal = {"BTC": Balance("BTC", D("0.005")), "BTCB2": Balance("BTCB2", D("0.5"))}
        s.orders._venues["nx"] = DryRunVenue(s.hub._venues["nx"], bal, clock=lambda: NOW)
        assert isinstance(await s.orders.prepare(buy(qty="0.5")), Pending)  # 0.003625 < 0.0043125

    async def test_order_size_without_other_venue_mid(self, tmp_path):
        """Without a venue's mid there is no total inventory: reject (fail closed)."""
        from xbt_terminal.adapters.dryrun import DryRunVenue

        s = Setup(tmp_path)
        bal = {"BTC": Balance("BTC", D("0.005"))}
        s.orders._venues["nx"] = DryRunVenue(s.hub._venues["nx"], bal, clock=lambda: NOW)
        await self.check(s, buy(), "order_size")

    async def test_mid_deviation(self, tmp_path):
        await self.check(Setup(tmp_path), buy(price="0.00800000"), "mid_deviation")

    async def test_venue_exposure(self, tmp_path):
        s = Setup(tmp_path, limits=replace(LIMITS, max_venue_exposure=D("0.008")))
        await self.check(s, buy(), "venue_exposure")

    async def test_self_cross(self, tmp_path):
        s = Setup(tmp_path)
        assert isinstance(
            await s.orders.confirm(await s.orders.prepare(sell(price="0.00726000"))), Sent
        )
        await self.check(s, buy(price="0.00727000"), "self_cross")

    async def test_tick_setting(self, tmp_path):
        await self.check(Setup(tmp_path), buy(price="0.007250001"), "tick")

    async def test_lot(self, tmp_path):
        await self.check(Setup(tmp_path), buy(qty="0.01005"), "lot")

    async def test_min_qty(self, tmp_path):
        await self.check(Setup(tmp_path), buy(qty="0.00005"), "min_qty")

    async def test_min_notional(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].spec = replace(spec("nk"), min_notional=D("0.001"))
        await self.check(s, buy(), "min_notional")

    async def test_stale_without_book(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].book.valid = False
        await self.check(s, buy(), "stale_data")

    async def test_insufficient_balance(self, tmp_path):
        await self.check(Setup(tmp_path, btc="0.00001"), buy(), "insufficient_balance")

    async def test_venue_without_keys(self, tmp_path):
        result = await self.check(Setup(tmp_path), buy(venue="nx"), "read_only")
        assert "sin keys" in result.message

    async def test_market_not_loaded(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].spec = None
        await self.check(s, buy(), "stale_data")

    async def test_message_with_btc_amounts(self, tmp_path):
        """The :.2f inherited from USDT showed 0.00 BTC (D-13)."""
        s = Setup(tmp_path, limits=replace(LIMITS, max_order_pct=D("1")))
        result = await s.orders.prepare(buy(qty="0.02"))
        assert "0.00014500" in result.message and "0.00 " not in result.message


@pytest.mark.spec("RF-10", "CA-04", "RNF-16", "RNF-24", "CA-18")
class TestFreshness:
    """A quiet book only counts if confirmed by the REST check (D-76); a live connection
    is no longer enough (D-13 corrected)."""

    async def test_quiet_book_confirmed_by_rest_counts(self, tmp_path):
        s = Setup(tmp_path, book_ts=NOW - 300_000)  # 5 min without changes, like NonKYC
        s.hub.state["nk"].checked_at = NOW - 4_000
        assert isinstance(await s.orders.prepare(buy()), Pending)

    async def test_quiet_book_with_recent_heartbeat_no_longer_counts(self, tmp_path):
        """That is how d-9820a4 fired: WS connected, no book changes for 2 h."""
        s = Setup(tmp_path, book_ts=NOW - 300_000)
        s.hub_now = NOW
        s.hub.apply("nk", ConnState("nk", "market", "ws", "up", 150.0, NOW))
        assert "stale_data" in codes(await s.orders.prepare(buy()))

    async def test_old_check_does_not_count(self, tmp_path):
        """With REST failing there are no confirmations: stale at stale_after_s."""
        s = Setup(tmp_path, book_ts=NOW - 300_000)
        s.hub.state["nk"].checked_at = NOW - 16_000
        assert "stale_data" in codes(await s.orders.prepare(buy()))

    async def test_book_in_doubt_is_invalid_even_if_recent(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].checked_at = NOW
        s.hub.state["nk"].suspect = True  # the last check did not match
        assert "stale_data" in codes(await s.orders.prepare(buy()))

    async def test_quiet_book_without_state_does_not_count(self, tmp_path):
        s = Setup(tmp_path, book_ts=NOW - 300_000)
        assert "stale_data" in codes(await s.orders.prepare(buy()))


@pytest.mark.spec("RF-10", "RF-13")
class TestUnreadableAccount:
    """D-16: if balances or orders cannot be read when validating, it rejects (no raise)."""

    @pytest.mark.parametrize(
        "error", [VenueError("NonKYC: HTTP 429: Too many requests"), httpx.ConnectError("sin red")]
    )
    async def test_unreadable_balances(self, tmp_path, error):
        s = Setup(tmp_path)

        async def boom():
            raise error

        s.venue.balances = boom
        result = await s.orders.prepare(buy())
        assert "venue_error" in codes(result)
        assert "no se pudieron leer" in result.message
        assert s.lines()[-1]["event"] == "reject_local"

    async def test_unreadable_orders_on_confirm(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(buy())

        async def boom():
            raise httpx.ReadTimeout("lento")

        s.venue.open_orders = boom
        result = await s.orders.confirm(p)
        assert isinstance(result, Rejected) and "venue_error" in result.codes
        assert [ln["event"] for ln in s.lines()] == ["reject_local"]


class Counting:
    """Count the venue reads OrderService does."""

    def __init__(self, venue):
        self.calls = []
        for name in ("balances", "open_orders"):
            real = getattr(venue, name)

            async def wrapped(_real=real, _name=name, **kw):
                self.calls.append(_name)
                return await _real(**kw)

            setattr(venue, name, wrapped)


def cached(tmp_path, *, age_ms=0, btc="0.005", orders=()):
    """Setup with AccountService already loaded (as after its first refresh)."""
    from xbt_terminal.services.account import AccountService

    s = Setup(tmp_path)
    account = AccountService({"nk": s.venue}, clock=lambda: NOW)
    state = account.state["nk"]
    state.balances = {"BTC": Balance("BTC", D(btc)), "BTCB2": Balance("BTCB2", D("0.5"))}
    state.balances_at = NOW - age_ms
    state.orders = {o.order_id: o for o in orders}
    ids = iter(f"t-{i:06d}" for i in range(1, 100))
    s.orders = OrderService(
        s.hub, {"nk": s.venue}, s.journal, LIMITS, clock=lambda: NOW,
        new_client_id=lambda: next(ids), account=account,
    )  # fmt: skip
    s.account = account
    s.counter = Counting(s.venue)
    return s


@pytest.mark.spec("RNF-11", "RF-10")
class TestCachedValidation:
    """D-18: with the account current, the venue is not asked on preparing or confirming."""

    async def test_fresh_cache_without_requests(self, tmp_path):
        s = cached(tmp_path, age_ms=29_000)
        p = await s.orders.prepare(buy())
        assert isinstance(p, Pending)
        await s.orders._validate(p.req)  # what confirm does before sending
        assert s.counter.calls == []

    async def test_cache_rules_the_checks(self, tmp_path):
        s = cached(tmp_path, btc="0.00001")  # the venue has 0.005; the account, almost nothing
        assert "insufficient_balance" in codes(await s.orders.prepare(buy()))

    async def test_old_cache_asks_the_venue(self, tmp_path):
        s = cached(tmp_path, age_ms=30_001)
        assert isinstance(await s.orders.prepare(buy()), Pending)
        assert sorted(s.counter.calls) == ["balances", "open_orders"]

    async def test_no_loaded_balances_asks_venue(self, tmp_path):
        s = cached(tmp_path)
        s.account.state["nk"].balances = None
        await s.orders.prepare(buy())
        assert "balances" in s.counter.calls

    async def test_account_orders_count(self, tmp_path):
        mine = Order("nk", "x-1", None, "sell", D("0.00726000"), D("0.01"), D("0"), "open", NOW)
        s = cached(tmp_path, orders=[mine])
        assert "self_cross" in codes(await s.orders.prepare(buy(price="0.00727000")))

    async def test_just_sent_order_counts_even_unseen_by_account(self, tmp_path):
        s = cached(tmp_path)
        sent = await s.orders.confirm(await s.orders.prepare(sell(price="0.00726000")))
        assert isinstance(sent, Sent) and s.account.state["nk"].orders == {}
        assert "self_cross" in codes(await s.orders.prepare(buy(price="0.00727000")))

    async def test_when_account_sees_it_finished_it_stops_counting(self, tmp_path):
        s = cached(tmp_path)
        sent = await s.orders.confirm(await s.orders.prepare(sell(price="0.00726000")))
        s.orders.on_private_event("nk", replace(sent.order, status="cancelled"))
        assert isinstance(await s.orders.prepare(buy(price="0.00727000")), Pending)


@pytest.mark.spec("RF-10")
async def test_insufficient_balance_with_8_decimals(tmp_path):
    """notional × (1 + fee) comes with many decimals; the message rounds to 8."""
    s = Setup(tmp_path, btc="0.00001")
    result = await s.orders.prepare(buy(qty="0.0137", price="0.00725001"))
    msg = next(v.message for v in result.violations if v.code == "insufficient_balance")
    assert msg == "hace falta 0.00009952 BTC, disponible 0.00001000"


def pct(side, value, price="0.00725000", venue="nk"):
    return PlaceOrder(venue, side, Percent(D(value)), D(price))


@pytest.mark.spec("RF-17", "RF-10")
class TestPercentQuantity:
    """D-21. Balances 0.005 BTC and 0.5 BTCB2; inventory 0.008625 BTC, cap 0.00215625."""

    async def test_buy_with_25_of_btc(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(pct("buy", "25"))
        assert isinstance(p, Pending)
        assert p.req.qty == D("0.1720")  # 0.00125 / (0.00725 × 1.002) = 0.17207…
        assert "BUY 0.1720 XBT" in p.summary
        assert "25 % de 0.00500000 BTC en NonKYC" in p.summary

    async def test_sell_with_20_of_btcb2(self, tmp_path):
        p = await Setup(tmp_path).orders.prepare(pct("sell", "20"))
        assert isinstance(p, Pending) and p.req.qty == D("0.1")
        assert "20 % de 0.50000000 XBT en NonKYC" in p.summary

    async def test_confirm_sends_the_fixed_quantity(self, tmp_path):
        s = Setup(tmp_path)
        p = await s.orders.prepare(pct("buy", "25"))
        assert isinstance(await s.orders.confirm(p), Sent)
        assert s.lines()[0]["data"]["qty"] == "0.1720"

    async def test_rules_apply_the_same(self, tmp_path):
        s = Setup(tmp_path)
        result = await s.orders.prepare(pct("buy", "50"))  # 0.0025 BTC > cap 0.00215625
        assert "order_size" in codes(result)
        last = s.lines()[-1]
        assert last["event"] == "reject_local" and last["data"]["qty"] == "0.3441"

    async def test_no_balance_rejected(self, tmp_path):
        s = Setup(tmp_path, btcb2="0")
        result = await s.orders.prepare(pct("sell", "100"))
        assert codes(result) == {"insufficient_balance"}
        assert "100 % de 0.00000000 XBT" in result.message and "lote" in result.message
        assert s.lines()[-1]["event"] == "reject_local"

    async def test_fine_lot_without_scientific_notation(self, tmp_path):
        s = Setup(tmp_path, btcb2="0")
        s.hub.state["nk"].spec = replace(spec("nk"), lot_size=D("1E-8"), min_qty=D("1E-8"))
        result = await s.orders.prepare(pct("sell", "100"))
        assert "un lote (0.00000001 XBT)" in result.message

    async def test_venue_without_keys(self, tmp_path):
        assert "read_only" in codes(
            await Setup(tmp_path).orders.prepare(pct("buy", "25", venue="nx"))
        )

    async def test_market_not_loaded(self, tmp_path):
        s = Setup(tmp_path)
        s.hub.state["nk"].spec = None
        assert "stale_data" in codes(await s.orders.prepare(pct("buy", "25")))

    async def test_unreadable_balances(self, tmp_path):
        s = Setup(tmp_path)

        async def boom():
            raise httpx.ConnectError("sin red")

        s.venue.balances = boom
        assert "venue_error" in codes(await s.orders.prepare(pct("buy", "25")))

    async def test_uses_account_balances(self, tmp_path):
        s = cached(tmp_path, btc="0.002")  # the venue has 0.005; the account, 0.002
        p = await s.orders.prepare(pct("buy", "25"))
        assert isinstance(p, Pending) and p.req.qty == D("0.0688")
        assert s.counter.calls == []
