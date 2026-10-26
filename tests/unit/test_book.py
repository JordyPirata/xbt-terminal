import pytest

from btcb2_terminal.core.book import BookRow, L2Book
from btcb2_terminal.core.models import BookDelta, BookSnapshot, D, Level


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
    def test_mejor_bid_ask_mid_y_spread(self, book):
        assert book.best_bid == D("999") and book.best_ask == D("1001")
        assert book.mid == D("1000") and book.spread == D("2")
        assert book.ts == 1_000

    def test_vacio_antes_del_primer_snapshot(self):
        b = L2Book("nk")
        assert not b.valid
        assert b.best_bid is None and b.mid is None and b.top(5) == ((), ())

    def test_el_snapshot_reemplaza_todo(self, book):
        book.apply(snap(seq=50, bids=(lv("900", "1"),), asks=(lv("910", "1"),)))
        assert book.best_bid == D("900") and book.depth == (1, 1)

    def test_snapshot_ignora_niveles_a_cero(self):
        b = L2Book("nk")
        b.apply(snap(bids=(lv("999", "0"), lv("998", "1"))))
        assert b.best_bid == D("998")

    def test_venue_equivocado(self, book):
        with pytest.raises(ValueError):
            book.apply(BookSnapshot("nx", (), (), 0, None))


@pytest.mark.spec("RF-02")
class TestTopConAcumulado:
    def test_bids_desc_asks_asc_con_acumulado(self, book):
        bids, asks = book.top(15)
        assert bids == (
            BookRow(D("999"), D("1"), D("1"), False),
            BookRow(D("998"), D("2"), D("3"), False),
            BookRow(D("997"), D("3"), D("6"), False),
        )
        assert [r.price for r in asks] == [D("1001"), D("1002")]
        assert asks[-1].cumulative == D("2.0")

    def test_n_niveles(self, book):
        bids, asks = book.top(2)
        assert len(bids) == 2 and len(asks) == 2

    def test_niveles_amm_marcados(self):
        b = L2Book("nk")
        b.apply(snap(asks=(lv("1001", "0.5"), lv("1003", "4", amm=True))))
        assert [r.amm for r in b.top(5)[1]] == [False, True]


@pytest.mark.spec("RF-02")
class TestDeltas:
    def test_actualiza_anade_y_borra(self, book):
        assert book.apply(
            delta(11, bids=(lv("999", "5"), lv("999.5", "1")), asks=(lv("1001", "0"),))
        )
        assert book.best_bid == D("999.5")
        assert book.best_ask == D("1002")
        assert book.top(1)[0][0].qty == D("1")
        assert book.ts == 2_000 and book.seq == 11

    def test_borrar_nivel_inexistente_no_falla(self, book):
        assert book.apply(delta(11, bids=(lv("500", "0"),)))

    def test_hueco_de_seq_invalida_hasta_nuevo_snapshot(self, book):
        assert not book.apply(delta(12))  # falta el 11
        assert not book.valid and book.needs_snapshot
        assert book.mid is None  # fallar cerrado: sin precio
        assert not book.apply(delta(13))  # se ignoran hasta el snapshot
        book.apply(snap(seq=20))
        assert book.valid and book.apply(delta(21))

    def test_delta_viejo_o_duplicado_se_ignora(self, book):
        assert book.apply(delta(11, bids=(lv("999", "5"),)))
        assert book.apply(delta(11, bids=(lv("999", "7"),)))
        assert book.apply(delta(9, bids=(lv("999", "8"),)))
        assert book.top(1)[0][0].qty == D("5")

    def test_sin_seq_se_aplica_siempre(self):
        b = L2Book("nk")
        b.apply(snap(seq=None))
        assert b.apply(delta(None, asks=(lv("1000.5", "1"),)))
        assert b.best_ask == D("1000.5")

    def test_delta_antes_del_snapshot(self):
        b = L2Book("nk")
        assert not b.apply(delta(1))
        assert b.needs_snapshot

    def test_book_cruzado_no_tiene_mid(self, book):
        book.apply(delta(11, bids=(lv("1001.5", "1"),)))
        assert book.mid is None and book.spread is None


@pytest.mark.spec("RNF-16")
class TestHoraDeRecepcion:
    def test_snapshot_y_delta_guardan_la_hora_local(self):
        b = L2Book("nk")
        b.apply(snap(ts=1_000), received_at=50_000)
        assert (b.ts, b.received_at) == (1_000, 50_000)
        b.apply(delta(11, bids=[lv("999", "4")], ts=2_000), received_at=51_000)
        assert (b.ts, b.received_at) == (2_000, 51_000)

    def test_un_delta_viejo_no_la_mueve(self, book):
        book.apply(snap(seq=10), received_at=50_000)
        book.apply(delta(9, ts=9_999), received_at=60_000)
        assert book.received_at == 50_000

    def test_sin_hora_local_queda_la_del_evento(self):
        b = L2Book("nk")
        b.apply(snap(ts=1_000))
        assert b.received_at == 1_000
