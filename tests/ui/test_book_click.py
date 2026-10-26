"""Clic en el book (T-21, RF-16, D-18): con la línea vacía se escribe la orden
entera, compra (clic en ventas) o venta (clic en compras), con el precio y la
cantidad del nivel, o la acumulada si el clic es en esa columna; con texto,
solo se inserta el precio en el cursor.

Books de FakeFeed: compras 0.00720000 × 1 y 0.00719900 × 2 (acumulado 1 y 3);
ventas 0.00730000 × 0.5 y 0.00730100 × 3 (acumulado 0.5 y 3.5)."""

import pytest

from btcb2_terminal.core.models import BookSnapshot, ConnState, D, Level
from btcb2_terminal.services.market import MarketHub
from btcb2_terminal.ui.panels import BookPanel, CommandLine

from ..fakes import FakeFeed, book
from .local import local_app
from .test_double_ui import Rig, until

# columnas de contenido: compras = acumulado 0-11, cantidad 12-23, precio 24-34;
# ventas = precio 40-50, cantidad 51-62, acumulado 63-74
BID_X, ASK_X = 30, 45  # columna de precio de cada lado
BID_CUM_X, ASK_CUM_X = 5, 65


async def ready(rig, pilot):
    """Books de FakeFeed con dos niveles por lado y spec cargado."""
    for vid in ("nk", "nx"):
        rig.feeds[vid].push(
            ConnState(vid, "market", "ws", "up", 100.0, rig.now),
            book(vid, "0.00720000", "0.00730000", ts=rig.now),
        )
    await until(pilot, lambda: all(rig.hub.state[v].ready for v in ("nk", "nx")))
    panel = rig.app.query_one("#book-nk", BookPanel)
    await until(pilot, lambda: "0.00730000" in panel.text)
    return panel


async def click(pilot, venue, x, y):
    """(x, y) en coordenadas de contenido; +1 por el borde del panel."""
    await pilot.click(f"#book-{venue}", offset=(x + 1, y + 1))
    await pilot.pause()


@pytest.mark.spec("RF-16")
class TestNivelBajoElRaton:
    async def test_mapa_de_filas_y_columnas(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            panel = await ready(rig, pilot)
            assert panel.level_at(BID_X, 0) is None  # cabecera
            assert panel.level_at(BID_X, 1) == ("bid", D("0.00720000"), D("1"))
            assert panel.level_at(BID_X, 2) == ("bid", D("0.00719900"), D("2"))
            assert panel.level_at(ASK_X, 1) == ("ask", D("0.00730000"), D("0.5"))
            assert panel.level_at(ASK_X, 2) == ("ask", D("0.00730100"), D("3"))
            for x in (37, 38, 39):  # separador " │ "
                assert panel.level_at(x, 1) is None, x
            assert panel.level_at(36, 1) is not None and panel.level_at(40, 1) is not None
            assert panel.level_at(BID_X, 3) is None  # nivel vacío
            assert panel.level_at(BID_X, 16) is None  # línea de resumen

    async def test_columna_de_acumulado_da_el_acumulado(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            panel = await ready(rig, pilot)
            assert panel.level_at(BID_CUM_X, 2) == ("bid", D("0.00719900"), D("3"))
            assert panel.level_at(11, 2)[2] == D("3")  # último carácter del acumulado
            assert panel.level_at(12, 2)[2] == D("2")  # ya es la cantidad
            assert panel.level_at(ASK_CUM_X, 2) == ("ask", D("0.00730100"), D("3.5"))
            assert panel.level_at(62, 2)[2] == D("3")  # cantidad
            assert panel.level_at(63, 2)[2] == D("3.5")  # acumulado
            assert panel.level_at(76, 2)[2] == D("3.5")  # marca AMM: cuenta como acumulado

    async def test_sin_book_no_hay_nivel(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            assert rig.app.query_one("#book-nk", BookPanel).level_at(BID_X, 1) is None


@pytest.mark.spec("RF-16", "RF-06")
class TestClic:
    async def test_clic_en_ventas_escribe_la_compra_entera(self, tmp_path, monkeypatch):
        rig = Rig(tmp_path)
        copied = []
        monkeypatch.setattr(rig.app, "copy_to_clipboard", copied.append)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nx", ASK_X, 1)
            cmd = rig.app.query_one(CommandLine)
            assert cmd.value == "buy nx 0.50000000 0.00730000"
            assert cmd.has_focus and cmd.cursor_position == len(cmd.value)
            assert copied == ["0.00730000"]
            assert "0.00730000" in rig.app.last_message

    async def test_clic_en_compras_escribe_la_venta(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", BID_X, 2)
            assert rig.app.query_one(CommandLine).value == "sell nk 2.0000 0.00719900"

    async def test_clic_en_acumulado_barre_hasta_ese_nivel(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", ASK_CUM_X, 2)
            assert rig.app.query_one(CommandLine).value == "buy nk 3.5000 0.00730100"

    async def test_enter_abre_el_dialogo(self, tmp_path):
        from btcb2_terminal.ui.app import ConfirmScreen

        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            small = BookSnapshot(
                "nk",
                (Level(D("0.00720000"), D("0.02")),),
                (Level(D("0.00730000"), D("0.01")),),
                rig.now,
            )
            rig.feeds["nk"].push(small)
            panel = rig.app.query_one("#book-nk", BookPanel)
            await until(pilot, lambda: panel.level_at(ASK_X, 1) == ("ask", D("0.0073"), D("0.01")))
            await click(pilot, "nk", ASK_X, 1)
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "BUY 0.0100 XBT @ 0.00730000 en NonKYC" in rig.app.screen.summary

    async def test_una_cantidad_por_encima_del_tope_no_se_recorta(self, tmp_path):
        """Acumulado 3.5 × 0.007301 ≈ 0.0256 BTC > 25 % del inventario (≈ 0.0043):
        lo dice RiskCheck, no se cambia a escondidas."""
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", ASK_CUM_X, 2)
            assert rig.app.query_one(CommandLine).value == "buy nk 3.5000 0.00730100"
            await pilot.press("enter")
            await until(pilot, lambda: "rechazada" in rig.app.last_message)
            assert "25 % del inventario total" in rig.app.last_message

    async def test_con_texto_solo_inserta_el_precio(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            cmd = rig.app.query_one(CommandLine)
            cmd.focus()
            cmd.value = "dbl 0.01 nk"
            cmd.cursor_position = len(cmd.value)
            await click(pilot, "nk", ASK_X, 1)
            assert cmd.value == "dbl 0.01 nk 0.00730000"
            cmd.value += " nx"
            cmd.cursor_position = len(cmd.value)
            await click(pilot, "nx", BID_X, 1)
            assert cmd.value == "dbl 0.01 nk 0.00730000 nx 0.00720000"

    async def test_sin_espacio_doble(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            cmd = rig.app.query_one(CommandLine)
            cmd.focus()
            cmd.value = "buy nk 0.01 "
            cmd.cursor_position = len(cmd.value)
            await click(pilot, "nk", BID_X, 1)
            assert cmd.value == "buy nk 0.01 0.00720000"

    async def test_clic_fuera_de_un_nivel_no_hace_nada(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await click(pilot, "nk", BID_X, 0)  # cabecera
            assert rig.app.query_one(CommandLine).value == ""


@pytest.mark.spec("RF-16", "RF-13")
async def test_en_solo_lectura_solo_copia(monkeypatch):
    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    app = local_app(MarketHub(feeds), book_levels=15, timeframe="1m", mode="read-only")
    copied = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
    async with app.run_test(size=(160, 45)) as pilot:
        feeds["nk"].push(book("nk", "0.00720000", "0.00730000"))
        panel = app.query_one("#book-nk", BookPanel)
        await until(pilot, lambda: "0.00730000" in panel.text)
        await click(pilot, "nk", ASK_X, 1)
        assert app.query_one(CommandLine).value == "" and copied == ["0.00730000"]
        assert "copiado" in app.last_message


@pytest.mark.spec("RF-16", "RF-28")
async def test_el_clic_copia_tambien_con_el_sistema(monkeypatch):
    """D-52: OSC 52 no llega en GNOME Terminal; el precio va también por xclip."""
    copied: list[str] = []

    async def clipboard(text):
        copied.append(text)
        return "xclip"

    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    app = local_app(
        MarketHub(feeds), book_levels=15, timeframe="1m", mode="read-only", clipboard=clipboard
    )
    monkeypatch.setattr(app, "copy_to_clipboard", lambda text: None)
    async with app.run_test(size=(160, 45)) as pilot:
        feeds["nk"].push(book("nk", "0.00720000", "0.00730000"))
        panel = app.query_one("#book-nk", BookPanel)
        await until(pilot, lambda: "0.00730000" in panel.text)
        await click(pilot, "nk", ASK_X, 1)
        await until(pilot, lambda: copied == ["0.00730000"])
