"""Reloj local y desfase con el venue (T-29, RNF-16, D-30).

La frescura del book se mide con la hora local de recepción, no con la del
exchange. En live, un venue con el reloj desfasado más de `max_clock_skew_s`, o
sin desfase medido, no recibe órdenes; en dry-run se valida igual."""

from dataclasses import replace

import pytest

from btcb2_terminal.services.orders import OrderService, Pending, Rejected

from .test_orders import LIMITS, NOW, Setup, buy, codes


def live(s, skew_ms, *, limits=LIMITS):
    """El OrderService de `s`, en live, con el desfase de NonKYC medido en `skew_ms`."""
    s.hub._venues["nk"].clock_skew_ms = skew_ms
    return OrderService(s.hub, {"nk": s.venue}, s.journal, limits, clock=lambda: NOW, live=True)


@pytest.mark.spec("RNF-16", "RF-10")
class TestFrescuraConRelojLocal:
    @pytest.mark.parametrize("server", [NOW - 60_000, NOW + 60_000])
    def test_hora_del_exchange_desfasada_no_cuenta(self, tmp_path, server):
        s = Setup(tmp_path)
        s.book("nk", "0.00720000", "0.00730000", ts=server, received=NOW)
        assert s.hub.state["nk"].book.received_at == NOW

    @pytest.mark.parametrize("server", [NOW - 60_000, NOW + 60_000])
    async def test_book_recien_recibido_vale(self, tmp_path, server):
        s = Setup(tmp_path)
        s.book("nk", "0.00720000", "0.00730000", ts=server, received=NOW - 1_000)
        assert isinstance(await s.orders.prepare(buy()), Pending)

    async def test_book_recibido_hace_rato_no_vale_aunque_el_exchange_diga_ahora(self, tmp_path):
        s = Setup(tmp_path)
        s.book("nk", "0.00720000", "0.00730000", ts=NOW, received=NOW - 20_000)
        assert "stale_data" in codes(await s.orders.prepare(buy()))


@pytest.mark.spec("RNF-16")
class TestDesfaseEnLive:
    async def test_dentro_del_tope_se_opera(self, tmp_path):
        s = Setup(tmp_path)
        assert isinstance(await live(s, 4_000).prepare(buy()), Pending)

    @pytest.mark.parametrize("skew", [6_000, -6_000])
    async def test_por_encima_del_tope_se_rechaza(self, tmp_path, skew):
        s = Setup(tmp_path)
        result = await live(s, skew).prepare(buy())
        assert isinstance(result, Rejected) and "clock_skew" in result.codes
        assert "6 s" in result.message and "NTP" in result.message

    async def test_sin_medir_se_rechaza(self, tmp_path):
        """Ante la duda, no se envía: sin cabecera Date no se sabe."""
        s = Setup(tmp_path)
        result = await live(s, None).prepare(buy())
        assert "clock_skew" in codes(result) and "sin medir" in result.message

    async def test_el_tope_sale_de_la_config(self, tmp_path):
        s = Setup(tmp_path)
        orders = live(s, 8_000, limits=replace(LIMITS, max_clock_skew_s=10))
        assert isinstance(await orders.prepare(buy()), Pending)

    async def test_en_dry_run_no_se_rechaza(self, tmp_path):
        s = Setup(tmp_path)
        s.hub._venues["nk"].clock_skew_ms = 60_000
        assert isinstance(await s.orders.prepare(buy()), Pending)

    def test_el_hub_da_el_desfase_del_adaptador(self, tmp_path):
        s = Setup(tmp_path)
        assert s.hub.clock_skew_ms("nk") is None  # el venue falso no lo mide
        s.hub._venues["nk"].clock_skew_ms = 1_500
        assert s.hub.clock_skew_ms("nk") == 1_500
