"""CA-17 (T-69, RF-32, D-69): pestaña "Valor de la cuenta" en la ventana de
resultados, con la serie del rango, los huecos sin interpolar y el resumen."""

import pytest
from textual.widgets import DataTable, TabbedContent

from xbt_terminal.core.models import D
from xbt_terminal.ui.panels import ValueChart
from xbt_terminal.ui.screens import results as results_module
from xbt_terminal.ui.screens.results import ResultsScreen

from ..unit.test_results import DAY, T0, plain_double
from ..unit.test_valuation import EVERY, value_row
from .test_orders_ui import until
from .test_results_ui import make_app, screen, write


async def open_values(pilot, app):
    await pilot.press("r")
    await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
    await pilot.press("tab")
    await until(pilot, lambda: screen(app).tab == "value")


@pytest.mark.spec("RF-32", "CA-17")
class TestPestanaValor:
    async def test_tab_cambia_de_pestana_y_vuelve(self, tmp_path):
        write(tmp_path, "20261022", plain_double("d-1", T0))
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await open_values(pilot, app)
            s = screen(app)
            assert s.query_one(TabbedContent).active == "value"
            assert "Valor de la cuenta" in str(s.query_one("#results-title").render())
            await pilot.press("tab")
            await until(pilot, lambda: screen(app).tab == "doubles")
            assert s.row_count() == 1  # la de dobles, intacta

    async def test_serie_del_rango_con_hueco_y_resumen(self, tmp_path):
        rows = [
            value_row(T0 - 2 * DAY, "9", nk="9"),  # fuera del día
            value_row(T0, "1.0", nk="0.6", nx="0.4"),
            value_row(T0 + EVERY, "1.2", nk="0.7", nx="0.5"),
            value_row(T0 + 5 * EVERY, "0.9", nk="0.5", nx="0.4"),  # tras un hueco
            value_row(T0 + 6 * EVERY, "1.1", nk="0.6", nx="0.5"),
        ]
        write(tmp_path, "20261020", rows[:1])
        write(tmp_path, "20261022", rows[1:])
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await open_values(pilot, app)
            s = screen(app)
            assert [v.value for v in s.report.values] == [D("1.0"), D("1.2"), D("0.9"), D("1.1")]
            chart = s.query_one("#value-total", ValueChart)
            assert [len(seg) for seg in chart.segments()] == [2, 2]
            text = s.value_text()
            assert "1.10000000 BTC" in text
            assert "+0.10000000 BTC (+10.00 %)" in text
            assert "máx 1.20000000" in text and "mín 0.90000000" in text
            assert "4 muestras" in text
            assert "NonKYC 0.60000000" in text and "Neoxa 0.50000000" in text
            assert "saltos" in text and "no es PnL" in text
            await pilot.press("3")
            await until(pilot, lambda: len(screen(app).report.values) == 5)
            assert screen(app).tab == "value"  # el rango no cambia de pestaña

    async def test_sin_muestras_lo_dice(self, tmp_path):
        write(tmp_path, "20261022", plain_double("d-1", T0))
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await open_values(pilot, app)
            assert "aún sin muestras" in screen(app).value_text()


@pytest.mark.spec("RF-32", "RF-25", "CA-17")
class TestRefresco:
    async def test_una_muestra_nueva_aparece_sin_cerrar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(results_module, "RESULTS_EVERY_S", 0.05)
        write(tmp_path, "20261022", [value_row(T0, "1.0")])
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await open_values(pilot, app)
            assert len(screen(app).report.values) == 1
            write(tmp_path, "20261022", [value_row(T0 + EVERY, "1.1")])
            await until(pilot, lambda: len(screen(app).report.values) == 2)
            assert "1.10000000 BTC" in screen(app).value_text()
            assert screen(app).tab == "value"

    async def test_la_seleccion_de_la_tabla_no_se_mueve(self, tmp_path, monkeypatch):
        monkeypatch.setattr(results_module, "RESULTS_EVERY_S", 0.05)
        write(tmp_path, "20261022", plain_double("d-1", T0) + plain_double("d-2", T0 + 60_000))
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("r")
            await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
            table = screen(app).query_one(DataTable)
            await pilot.press("down")  # d-1: la segunda, la más reciente va arriba
            await until(pilot, lambda: table.cursor_row == 1)
            # sin cambios: no se redibuja
            await pilot.pause(0.2)
            assert table.cursor_row == 1
            # una doble nueva arriba: la selección sigue en d-1
            write(tmp_path, "20261022", plain_double("d-3", T0 + 120_000))
            await until(pilot, lambda: table.row_count == 3)
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            assert row_key.value == "d-1"


@pytest.mark.spec("RF-32", "CA-17")
class TestTramo:
    async def test_z_ajusta_al_ultimo_tramo_y_vuelve(self, tmp_path):
        rows = [
            value_row(T0, "1.0", nk="0.6", nx="0.4"),
            value_row(T0 + EVERY, "0.5", nk="0.1", nx="0.4"),  # transferencia en tránsito
            value_row(T0 + 2 * EVERY, "1.0", nk="0.1", nx="0.9"),  # llega
            value_row(T0 + 3 * EVERY, "1.00001", nk="0.1", nx="0.90001"),
        ]
        write(tmp_path, "20261022", rows)
        app = make_app(tmp_path)
        async with app.run_test(size=(160, 45)) as pilot:
            await open_values(pilot, app)
            total = screen(app).query_one("#value-total", ValueChart)
            assert len(total.values) == 4
            await pilot.press("z")
            await until(pilot, lambda: len(total.values) == 2)
            venues = screen(app).query_one("#value-venues", ValueChart)
            assert len(venues.values) == 2
            text = screen(app).value_text()
            assert "último tramo desde 10-22 12:10" in text and "+1000 sats" in text
            assert "[z]" in str(screen(app).query_one("#results-title").render())
            await pilot.press("z")
            await until(pilot, lambda: len(total.values) == 4)
            assert "último tramo" not in screen(app).value_text()
