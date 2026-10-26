"""Book centrado con barras de profundidad a los lados (RF-02, D-49): la
escalera va en el centro del panel; a su izquierda, una barra verde por nivel
de compra que crece hacia fuera con el acumulado, y a su derecha la de ventas.
Las barras usan el sitio que sobra: con el panel justo no hay barras.

Books de FakeFeed: compras 0.00720000 × 1 y 0.00719900 × 2 (acumulado 1 y 3);
ventas 0.00730000 × 0.5 y 0.00730100 × 3 (acumulado 0.5 y 3.5)."""

import pytest

from btcb2_terminal.core.models import D
from btcb2_terminal.ui.panels import LADDER_W, BookPanel, depth_bar

from .test_book_click import ASK_X, BID_CUM_X, BID_X, ready
from .test_double_ui import Rig

WIDE = (220, 50)  # cada book con ~108 columnas de contenido


def line_with(panel: BookPanel, needle: str) -> str:
    return next(ln for ln in panel.text.splitlines() if needle in ln)


@pytest.mark.spec("RF-02")
class TestBarra:
    def test_compras_crecen_hacia_la_izquierda(self):
        assert depth_bar(D(1), 4, "bid") == "████"
        assert depth_bar(D("0.5"), 4, "bid") == "  ██"
        assert depth_bar(D("0.625"), 4, "bid") == " ▐██"

    def test_ventas_crecen_hacia_la_derecha(self):
        assert depth_bar(D("0.5"), 4, "ask") == "██  "
        assert depth_bar(D("0.625"), 4, "ask") == "██▌ "

    def test_un_nivel_con_algo_siempre_se_ve(self):
        assert depth_bar(D("0.001"), 4, "ask") == "▌   "
        assert depth_bar(D(0), 4, "ask") == "    "

    def test_sin_sitio_no_hay_barra(self):
        assert depth_bar(D(1), 0, "bid") == ""


@pytest.mark.spec("RF-02", "RF-16")
class TestPanel:
    async def test_barras_a_los_lados_proporcionales_al_acumulado(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            first, second = line_with(panel, "0.00720000"), line_with(panel, "0.00719900")
            # compras a la izquierda de la escalera, ventas a la derecha
            assert first.index("█") < first.index("0.00720000")
            assert first.rindex("█") > first.index("0.00730000")
            # el nivel más profundo (acumulado 3.5, el máximo) llena su barra
            bar_w = panel.bar_width
            assert bar_w >= 8 and second.rstrip().endswith("█" * bar_w)
            assert second.count("█") > first.count("█")

    async def test_escalera_centrada(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            width = panel.content_size.width
            left = panel.ladder_x
            right = width - left - LADDER_W
            assert abs(left - right) <= 1

    async def test_el_clic_se_desplaza_con_la_escalera(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            x0 = panel.ladder_x
            assert x0 > 0
            assert panel.level_at(x0 + BID_X, 2) == ("bid", D("0.00719900"), D("2"))
            assert panel.level_at(x0 + ASK_X, 1) == ("ask", D("0.00730000"), D("0.5"))
            assert panel.level_at(x0 + BID_CUM_X, 2)[2] == D("3")

    async def test_clic_en_la_barra_da_el_acumulado(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=WIDE) as pilot:
            panel = await ready(rig, pilot)
            x0 = panel.ladder_x
            assert panel.level_at(x0 - 2, 2) == ("bid", D("0.00719900"), D("3"))
            assert panel.level_at(x0 + LADDER_W + 2, 2) == ("ask", D("0.00730100"), D("3.5"))

    async def test_panel_justo_sin_barras(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            panel = await ready(rig, pilot)
            assert panel.bar_width == 0 and "█" not in panel.text
