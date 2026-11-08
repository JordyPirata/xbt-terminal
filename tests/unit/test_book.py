import pytest

from xbt_terminal.core.book import BookRow, L2Book
from xbt_terminal.core.models import BookDelta, BookSnapshot, D, Level


def lv(price: str, qty: str, amm: bool = False) -> Level:
    return Level(D(price), D(qty), amm)


def snap(seq=10, ts=1_000, bids=None, asks=None) -> BookSnapshot:
    bids = bids if bids is not None else (lv("999", "1"), lv("998", "2"), lv("997", "3"))
    asks = asks if asks is not None else (lv("1001", "0.5"), lv("1002", "1.5"))
    return BookSnapshot("nk", tuple(bids), tuple(asks), ts, seq)


def delta(seq, bids=(), asks=(), ts=2_000) -> BookDelta:
    return BookDelta("nk", tuple(bids), tuple(asks), ts, seq)


@pytest.fixture
def book() -> L2Book:
    b = L2Book("nk")
    b.apply(snap())
    return b


@pytest.mark.spec("RF-02")
class TestSnapshot:
    def test_best_bid_ask_mid_and_spread(self, book):
        assert book.best_bid == D("999") and book.best_ask == D("1001")
        assert book.mid == D("1000") and book.spread == D("2")
        assert book.ts == 1_000

    def test_empty_before_first_snapshot(self):
        b = L2Book("nk")
        assert not b.valid
        assert b.best_bid is None and b.mid is None and b.top(5) == ((), ())

    def test_snapshot_replaces_everything(self, book):
        book.apply(snap(seq=50, bids=(lv("900", "1"),), asks=(lv("910", "1"),)))
        assert book.best_bid == D("900") and book.depth == (1, 1)

    def test_snapshot_ignores_zero_levels(self):
        b = L2Book("nk")
        b.apply(snap(bids=(lv("999", "0"), lv("998", "1"))))
        assert b.best_bid == D("998")

    def test_wrong_venue(self, book):
        with pytest.raises(ValueError):
            book.apply(BookSnapshot("nx", (), (), 0, None))


@pytest.mark.spec("RF-02")
class TestTopWithCumulative:
    def test_bids_desc_asks_asc_with_cumulative(self, book):
        bids, asks = book.top(15)
        assert bids == (
            BookRow(D("999"), D("1"), D("1"), False),
            BookRow(D("998"), D("2"), D("3"), False),
            BookRow(D("997"), D("3"), D("6"), False),
        )
        assert [r.price for r in asks] == [D("1001"), D("1002")]
        assert asks[-1].cumulative == D("2.0")

    def test_n_levels(self, book):
        bids, asks = book.top(2)
        assert len(bids) == 2 and len(asks) == 2

    def test_amm_levels_marked(self):
        b = L2Book("nk")
        b.apply(snap(asks=(lv("1001", "0.5"), lv("1003", "4", amm=True))))
        assert [r.amm for r in b.top(5)[1]] == [False, True]


@pytest.mark.spec("RF-02", "RNF-23")
class TestBestLevels:
    """The book's own levels, without the cumulative: what the bot reads (T-92)."""

    def test_same_order_and_levels_as_top(self, book):
        book.apply(delta(11, asks=[lv("1003", "4", amm=True)]))
        bids, asks = book.best_levels(2)
        top_bids, top_asks = book.top(2)
        assert [(lv.price, lv.qty, lv.amm) for lv in bids] == [
            (r.price, r.qty, r.amm) for r in top_bids
        ]
        assert [(lv.price, lv.qty, lv.amm) for lv in asks] == [
            (r.price, r.qty, r.amm) for r in top_asks
        ]

    def test_are_the_applied_levels_not_copies(self, book):
        level = lv("1000.5", "7")
        book.apply(delta(11, asks=[level]))
        assert book.best_levels(1)[1][0] is level

    def test_invalid_book_gives_nothing(self, book):
        book.apply(delta(13))  # skips 11 and 12
        assert book.best_levels(5) == ((), ())


@pytest.mark.spec("RF-02")
class TestDeltas:
    def test_updates_adds_and_deletes(self, book):
        assert book.apply(
            delta(11, bids=(lv("999", "5"), lv("999.5", "1")), asks=(lv("1001", "0"),))
        )
        assert book.best_bid == D("999.5")
        assert book.best_ask == D("1002")
        assert book.top(1)[0][0].qty == D("1")
        assert book.ts == 2_000 and book.seq == 11

    def test_deleting_missing_level_does_not_fail(self, book):
        assert book.apply(delta(11, bids=(lv("500", "0"),)))

    def test_seq_gap_invalidates_until_new_snapshot(self, book):
        assert not book.apply(delta(12))  # 11 is missing
        assert not book.valid and book.needs_snapshot
        assert book.mid is None  # fail closed: no price
        assert not book.apply(delta(13))  # ignored until the snapshot
        book.apply(snap(seq=20))
        assert book.valid and book.apply(delta(21))

    def test_old_or_duplicate_delta_ignored(self, book):
        assert book.apply(delta(11, bids=(lv("999", "5"),)))
        assert book.apply(delta(11, bids=(lv("999", "7"),)))
        assert book.apply(delta(9, bids=(lv("999", "8"),)))
        assert book.top(1)[0][0].qty == D("5")

    def test_no_seq_always_applies(self):
        b = L2Book("nk")
        b.apply(snap(seq=None))
        assert b.apply(delta(None, asks=(lv("1000.5", "1"),)))
        assert b.best_ask == D("1000.5")

    def test_delta_before_snapshot(self):
        b = L2Book("nk")
        assert not b.apply(delta(1))
        assert b.needs_snapshot

    def test_crossed_book_has_no_mid(self, book):
        book.apply(delta(11, bids=(lv("1001.5", "1"),)))
        assert book.mid is None and book.spread is None


@pytest.mark.spec("RNF-16")
class TestArrivalTime:
    def test_snapshot_and_delta_keep_local_time(self):
        b = L2Book("nk")
        b.apply(snap(ts=1_000), received_at=50_000)
        assert (b.ts, b.received_at) == (1_000, 50_000)
        b.apply(delta(11, bids=[lv("999", "4")], ts=2_000), received_at=51_000)
        assert (b.ts, b.received_at) == (2_000, 51_000)

    def test_old_delta_does_not_move_it(self, book):
        book.apply(snap(seq=10), received_at=50_000)
        book.apply(delta(9, ts=9_999), received_at=60_000)
        assert book.received_at == 50_000

    def test_without_local_time_event_time_stays(self):
        b = L2Book("nk")
        b.apply(snap(ts=1_000))
        assert b.received_at == 1_000
