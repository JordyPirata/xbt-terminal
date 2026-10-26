"""Desfase de reloj con cada venue (T-29, RNF-16, D-30): se mide con la cabecera
`Date` de cada respuesta REST, contra la hora local de la petición."""

import httpx
import pytest

from xbt_terminal.adapters.streaming import skew_ms

from .test_supervisor import FakeVenue

DATE = "Thu, 22 Oct 2026 02:07:27 GMT"
DATE_MS = 1_792_634_847_000  # ese segundo en ms epoch


@pytest.mark.spec("RNF-16")
class TestSkew:
    def test_reloj_en_hora(self):
        """`Date` trunca al segundo: se toma la mitad del segundo."""
        assert skew_ms(DATE, DATE_MS + 400, DATE_MS + 600) == 0

    @pytest.mark.parametrize("off", [60_000, -60_000])
    def test_reloj_local_desfasado(self, off):
        local = DATE_MS + 500 - off
        assert skew_ms(DATE, local - 100, local + 100) == off

    @pytest.mark.parametrize("bad", [None, "", "ayer", "Thu, 99 Oct 2026 02:07:27 GMT"])
    def test_sin_cabecera_legible(self, bad):
        assert skew_ms(bad, DATE_MS, DATE_MS) is None


@pytest.mark.spec("RNF-16")
class TestEnElAdaptador:
    async def test_cada_respuesta_rest_lo_actualiza(self):
        now = [DATE_MS + 500 - 30_000]
        venue = FakeVenue(clock=lambda: now[0])
        assert venue.clock_skew_ms is None

        async def reply():
            return httpx.Response(200, headers={"date": DATE})

        await venue.timed(reply())
        assert venue.clock_skew_ms == 30_000

    async def test_lo_que_no_es_una_respuesta_no_lo_toca(self):
        venue = FakeVenue()

        async def other():
            return {"ok": True}

        await venue.timed(other())
        assert venue.clock_skew_ms is None
