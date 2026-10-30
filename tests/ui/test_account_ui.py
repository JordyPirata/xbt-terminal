"""Paneles de cuenta (T-19): balances e inventario neto, órdenes abiertas y
cancelar una, por venue o todas, siempre con confirmación (RF-05, RF-08, CA-05)."""

import pytest

from xbt_terminal.core.models import Balance, D, OrderRequest
from xbt_terminal.services.market import MarketHub
from xbt_terminal.ui.panels import BalancesPanel, ChartPanel, CommandLine
from xbt_terminal.ui.screens.confirm import ConfirmScreen
from xbt_terminal.ui.screens.orders import OrdersScreen

from ..fakes import FakeFeed
from .local import local_app
from .test_double_ui import Rig, ready, until


async def place(rig, venue, side="buy", price="0.00725000", qty="0.01"):
    req = OrderRequest(venue, side, D(qty), D(price), f"t-{venue}{side}")
    return await rig.venues[venue].place_limit(req)


async def command(rig, pilot, text):
    cmd = rig.app.query_one(CommandLine)
    cmd.focus()
    cmd.value = text
    rig.app.last_message = ""
    await pilot.press("enter")


def total_open(rig):
    return sum(len(st.orders) for st in rig.account.state.values())


@pytest.mark.spec("RF-05")
class TestBalances:
    async def test_por_venue_e_inventario_total(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "Neoxa" in panel.text and "valor" in panel.text)
            nk = next(ln for ln in panel.text.splitlines() if "NonKYC" in ln)
            assert "BTC 0.00500000 · XBT 0.50000000 ·" in nk  # nada en órdenes (D-67)
            # 0.005 + 0.5 × 0.00725 (mid de NonKYC)
            assert "valor 0.00862500 BTC" in nk
            title = panel.border_title or ""
            assert "BTC 0.01000000" in title and "XBT 1.00000000" in title
            assert "valor 0.01726000 BTC" in title  # + 0.005 + 0.5 × 0.00727 en Neoxa

    async def test_saldo_con_mas_decimales_que_el_lote(self, tmp_path):
        """NonKYC opera BTCB2 con 4 decimales, pero el saldo real puede tener 8."""
        rig = Rig(tmp_path)
        rig.venues["nk"]._fixed["BTCB2"] = Balance("BTCB2", D("0.09040708"))
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "XBT 0.09040708" in panel.text)

    async def test_una_orden_mueve_el_saldo_a_en_ordenes(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "valor" in panel.text)
            await place(rig, "nx", side="sell", qty="0.2", price="0.00730000")
            await until(pilot, lambda: "XBT 0.30000000 + 0.20000000" in panel.text, timeout=3)

    async def test_un_fill_refresca_enseguida(self, tmp_path):
        """RF-05: tras un fill no se espera al refresco de 10 s."""
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "valor" in panel.text)
            order = await place(rig, "nk", qty="0.01")
            await rig.venues["nk"].simulate_fill(order.order_id, D("0.01"))
            await until(pilot, lambda: "XBT 0.51000000 ·" in panel.text, timeout=1.5)
            assert "+ 0.00000000" not in panel.text  # sin órdenes, no sale (D-67)

    async def test_solo_lectura_sin_panel(self, tmp_path):
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        app = local_app(MarketHub(feeds), book_levels=15, timeframe="1m", mode="read-only")
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            assert not app.query_one(BalancesPanel).display

    async def test_cabe_con_todo_a_160x45(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "valor" in panel.text)
            assert all(len(ln) <= 156 for ln in panel.text.splitlines())
            assert all(c.region.height >= 8 for c in rig.app.query(ChartPanel))


@pytest.mark.spec("RF-08")
class TestOrdenesAbiertas:
    async def test_o_abre_la_lista(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            a = await place(rig, "nk")
            b = await place(rig, "nx", side="sell", price="0.00730000")
            await until(pilot, lambda: total_open(rig) == 2)
            await pilot.press("o")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            screen = rig.app.screen
            assert screen.order_ids == [a.order_id, b.order_id]
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(rig.app.screen, OrdersScreen))

    async def test_orders_de_un_venue(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await place(rig, "nk")
            b = await place(rig, "nx", side="sell", price="0.00730000")
            await until(pilot, lambda: total_open(rig) == 2)
            await command(rig, pilot, "orders nx")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            assert rig.app.screen.order_ids == [b.order_id]

    async def test_cancelar_la_seleccionada_con_confirmacion(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            a = await place(rig, "nk")
            await until(pilot, lambda: total_open(rig) == 1)
            await pilot.press("o")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            await pilot.press("c")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert a.order_id in rig.app.screen.summary
            await pilot.press("y")
            await until(pilot, lambda: total_open(rig) == 0, timeout=3)
            assert "cancelado" in rig.app.last_message
            assert await rig.venues["nk"].open_orders() == []
            assert rig.events()[-2:] == ["cancel_sent", "cancel_ack"]

    async def test_la_lista_se_actualiza_sola(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await pilot.press("o")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            assert rig.app.screen.order_ids == []
            a = await place(rig, "nk")
            await until(pilot, lambda: rig.app.screen.order_ids == [a.order_id], timeout=3)


@pytest.mark.spec("RF-08", "CA-05")
class TestCancelarTodo:
    async def test_x_cancela_todo_tras_confirmar(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await place(rig, "nk")
            await place(rig, "nx", side="sell", price="0.00730000")
            await until(pilot, lambda: total_open(rig) == 2)
            await pilot.press("x")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "cancelar 2 órdenes" in rig.app.screen.summary
            await pilot.press("y")
            await until(pilot, lambda: "quedan 0" in rig.app.last_message, timeout=3)
            for venue in rig.venues.values():
                assert await venue.open_orders() == []

    @pytest.mark.parametrize("key", ["n", "escape", "enter"])
    async def test_por_defecto_no_cancela(self, tmp_path, key):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await place(rig, "nk")
            await until(pilot, lambda: total_open(rig) == 1)
            await pilot.press("x")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press(key)
            await until(pilot, lambda: "no se canceló nada" in rig.app.last_message)
            assert len(await rig.venues["nk"].open_orders()) == 1

    async def test_comandos_cancel(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            a = await place(rig, "nk")
            await place(rig, "nx", side="sell", price="0.00730000")
            await command(rig, pilot, f"cancel {a.order_id}")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "cancelado" in rig.app.last_message, timeout=3)
            await command(rig, pilot, "cancel nx")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "en Neoxa" in rig.app.screen.summary
            await pilot.press("y")
            await until(pilot, lambda: "cancelado" in rig.app.last_message, timeout=3)
            assert [len(await v.open_orders()) for v in rig.venues.values()] == [0, 0]

    async def test_sin_ordenes_lo_dice_sin_dialogo(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await pilot.press("x")
            await until(pilot, lambda: "no hay órdenes abiertas" in rig.app.last_message)
            assert not isinstance(rig.app.screen, ConfirmScreen)


@pytest.mark.spec("RF-07")
async def test_d_empieza_una_orden_doble(tmp_path):
    rig = Rig(tmp_path)
    async with rig.app.run_test(size=(160, 45)) as pilot:
        await pilot.press("d")
        cmd = rig.app.query_one(CommandLine)
        await until(pilot, lambda: cmd.has_focus and cmd.value == "dbl ")


@pytest.mark.spec("RF-06", "RF-08")
async def test_las_teclas_entre_corchetes_se_ven(tmp_path):
    """Sin markup=False, Rich se come "[y]" y el diálogo no dice cómo confirmar."""
    from textual.widgets import Static

    rig = Rig(tmp_path)
    async with rig.app.run_test(size=(160, 45)) as pilot:
        await ready(rig, pilot)
        await place(rig, "nk")
        await until(pilot, lambda: total_open(rig) == 1)
        await pilot.press("x")
        await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
        await pilot.pause()
        assert "[y] confirmar" in str(rig.app.screen.query_one(Static).render())
        await pilot.press("n")
        await pilot.press("o")
        await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
        await pilot.pause()
        header = str(rig.app.screen.query_one(Static).render())
        assert "[c] cancelar la seleccionada" in header and "[Esc] cerrar" in header


@pytest.mark.spec("RF-06")
@pytest.mark.parametrize(
    "text", ["login rechazado [1002]", "campo [red] fin", "suelto [/bold] fin"]
)
async def test_un_mensaje_con_corchetes_se_ve_tal_cual(tmp_path, text):
    """Los errores del venue pueden traer corchetes: con markup, "[red]" desaparece
    y "[/bold]" lanza MarkupError al pintar el aviso."""
    from textual.content import Content

    rig = Rig(tmp_path)
    async with rig.app.run_test(size=(160, 45)) as pilot:
        rig.app.say(text, error=True)
        await pilot.pause()
        (note,) = list(rig.app._notifications)
        # lo mismo que hace Toast.render
        shown = Content.from_markup(note.message) if note.markup else Content(note.message)
        assert shown.plain == text
