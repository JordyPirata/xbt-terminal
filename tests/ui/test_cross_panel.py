"""Panel cruzado en pantalla (T-14b). Las cifras esperadas se calcularon a mano
con Decimal sobre los precios reales de `tests/unit/test_cross.py` (2026-10-20):

NonKYC  bid 0.00727748  ask 0.00730299
Neoxa   bid 0.00735714  ask 0.00742999      taker 0.2 % en los dos
"""

import asyncio

import pytest

from btcb2_terminal.core.cross import Quote
from btcb2_terminal.core.models import BookDelta, BookSnapshot, D, Level
from btcb2_terminal.services.market import MarketHub
from btcb2_terminal.ui.panels import CrossPanel, render_cross

from ..fakes import T0, FakeFeed
from .local import local_app

NK = Quote("nk", D("0.00727748"), D("0.00730299"), taker_fee=D("0.002"))
NX = Quote("nx", D("0.00735714"), D("0.00742999"), taker_fee=D("0.002"))


def line(text: str, start: str) -> str:
    return next(ln for ln in text.splitlines() if ln.startswith(start))


@pytest.mark.spec("RF-04")
class TestCifras:
    def test_mid_y_spread_por_venue(self):
        text = render_cross(NK, NX).plain
        nk, nx = line(text, "NonKYC"), line(text, "Neoxa")
        assert "mid 0.00729024" in nk and "spread 0.00002551 · 2551 sats · 0.35 %" in nk
        assert "mid 0.00739356" in nx and "spread 0.00007285 · 7285 sats · 0.99 %" in nx

    def test_diferencia_de_mids(self):
        text = render_cross(NK, NX).plain
        assert "nx - nk  +0.00010333 · +10333 sats · +1.42 %" in text

    def test_ruta_comprar_nk_vender_nx(self):
        route = line(render_cross(NK, NX).plain, "NonKYC").split("│")[1]
        assert "nk→nx" in route
        assert "bruto +0.00005415 · +5415 sats · +0.74 %" in route
        assert "neto +0.00002483 · +2483 sats · +0.34 %" in route

    def test_ruta_comprar_nx_vender_nk(self):
        route = line(render_cross(NK, NX).plain, "Neoxa").split("│")[1]
        assert "nx→nk" in route
        assert "bruto -0.00015251 · -15251 sats · -2.05 %" in route
        assert "neto -0.00018192 · -18192 sats · -2.45 %" in route

    def test_neto_positivo_resaltado(self):
        text = render_cross(NK, NX)
        styles = {text.plain[s.start : s.end]: str(s.style) for s in text.spans}
        positive = next(k for k in styles if k.startswith("neto +"))
        negative = next(k for k in styles if k.startswith("neto -"))
        assert "green" in styles[positive] and "bold" in styles[positive]
        assert "green" not in styles[negative]


@pytest.mark.spec("RF-04")
class TestDatosParciales:
    def test_venue_sin_datos(self):
        text = render_cross(NK, None).plain
        assert "mid 0.00729024" in line(text, "NonKYC")
        nx = line(text, "Neoxa")
        assert "mid —" in nx and "spread —" in nx
        assert "nx - nk  —" in text
        assert "bruto —" in text and "+5415" not in text

    def test_falta_un_lado(self):
        sin_ask_nk = Quote("nk", NK.best_bid, None, taker_fee=NK.taker_fee)
        text = render_cross(sin_ask_nk, NX).plain
        assert "mid —" in line(text, "NonKYC")
        assert "nk→nx  bruto —" in text  # sin ask en nk no se puede comprar ahí
        assert "nx→nk  bruto -0.00015251" in text  # vender en nk solo necesita su bid

    def test_ninguno(self):
        text = render_cross(None, None).plain
        assert "0.0" not in text and text.count("—") >= 6


def snapshot(venue, bid, ask, seq=1):
    return BookSnapshot(venue, (Level(D(bid), D("1")),), (Level(D(ask), D("1")),), T0, seq)


async def until(pilot, predicate, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await pilot.pause(0.01)


@pytest.mark.spec("RF-04", "RNF-02")
class TestEnPantalla:
    async def test_se_actualiza_con_los_books(self):
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        hub = MarketHub(feeds, history=50, retry_s=0.01, clock=lambda: T0)
        app = local_app(hub, book_levels=15, timeframe="1m", mode="read-only")
        async with app.run_test(size=(160, 45)) as pilot:
            panel = app.query_one(CrossPanel)
            feeds["nk"].push(snapshot("nk", "0.00727748", "0.00730299"))
            feeds["nx"].push(snapshot("nx", "0.00735714", "0.00742999"))
            await until(pilot, lambda: "+5415 sats" in panel.text)
            assert "neto +0.00002483" in panel.text  # fee taker 0.2 % del spec del venue
            width = panel.content_region.width
            assert all(len(ln) <= width for ln in panel.text.splitlines())

            # desaparece el bid de nx: ya no se puede vender ahí (ruta nk→nx)
            feeds["nx"].push(BookDelta("nx", (Level(D("0.00735714"), D("0")),), (), T0, 2))
            await until(pilot, lambda: "nk→nx  bruto —" in panel.text)

    async def test_sustituye_al_aviso_de_pendiente(self):
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        app = local_app(MarketHub(feeds), book_levels=15, timeframe="1m", mode="dry-run")
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            panel = app.query_one(CrossPanel)
            assert "T-14b" not in panel.text  # el modo vive en la barra de estado (T-15)


@pytest.mark.spec("RF-24", "RF-04")
async def test_descuento_de_fee_solo_en_el_panel():
    """Neoxa con fees pagadas en su token (D-38): el panel calcula la neta con la
    taker al 50 %; el spec del hub, que usan el bot y RiskCheck, no cambia."""
    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    hub = MarketHub(feeds, history=50, retry_s=0.01, clock=lambda: T0)
    app = local_app(
        hub, book_levels=15, timeframe="1m", mode="read-only", fee_discounts={"nx": D("50")}
    )
    async with app.run_test(size=(160, 45)) as pilot:
        panel = app.query_one(CrossPanel)
        feeds["nk"].push(snapshot("nk", "0.00727748", "0.00730299"))
        feeds["nx"].push(snapshot("nx", "0.00735714", "0.00742999"))
        await until(pilot, lambda: "+5415 sats" in panel.text)
        # 0.00735714 × (1 - 0.001) - 0.00730299 × (1 + 0.002)
        assert "neto +0.00003219" in panel.text
        assert "Neoxa con fee -50 %" in panel.text
        assert hub.state["nx"].spec.taker_fee == D("0.002")
        width = panel.content_region.width
        assert all(len(ln) <= width for ln in panel.text.splitlines())
