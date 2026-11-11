"""Account panels (T-19): balances and net inventory, open orders and cancelling one, by
venue or all, always with confirmation (RF-05, RF-08, CA-05)."""

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
    order_request = OrderRequest(venue, side, D(qty), D(price), f"t-{venue}{side}")
    return await rig.venues[venue].place_limit(order_request)


async def command(rig, pilot, text):
    command_line = rig.app.query_one(CommandLine)
    command_line.focus()
    command_line.value = text
    rig.app.last_message = ""
    await pilot.press("enter")


def total_open(rig):
    return sum(len(state.orders) for state in rig.account.state.values())


@pytest.mark.spec("RF-05")
class TestBalances:
    async def test_per_venue_and_total_inventory(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "Neoxa" in panel.text and "valor" in panel.text)
            nk = next(ln for ln in panel.text.splitlines() if "NonKYC" in ln)
            assert "BTC 0.00500000 · XBT 0.50000000 ·" in nk  # nothing in orders (D-67)
            # 0.005 + 0.5 × 0.00725 (NonKYC mid)
            assert "valor 0.00862500 BTC" in nk
            title = panel.border_title or ""
            assert "BTC 0.01000000" in title and "XBT 1.00000000" in title
            assert "valor 0.01726000 BTC" in title  # + 0.005 + 0.5 × 0.00727 on Neoxa

    async def test_balance_with_more_decimals_than_lot(self, tmp_path):
        """NonKYC trades BTCB2 with 4 decimals, but the real balance may have 8."""
        rig = Rig(tmp_path)
        rig.venues["nk"]._fixed["BTCB2"] = Balance("BTCB2", D("0.09040708"))
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "XBT 0.09040708" in panel.text)

    async def test_order_moves_balance_to_in_orders(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "valor" in panel.text)
            await place(rig, "nx", side="sell", qty="0.2", price="0.00730000")
            await until(pilot, lambda: "XBT 0.30000000 + 0.20000000" in panel.text, timeout=3)

    async def test_fill_refreshes_at_once(self, tmp_path):
        """RF-05: after a fill there is no waiting for the 10 s refresh."""
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "valor" in panel.text)
            order = await place(rig, "nk", qty="0.01")
            await rig.venues["nk"].simulate_fill(order.order_id, D("0.01"))
            await until(pilot, lambda: "XBT 0.51000000 ·" in panel.text, timeout=1.5)
            assert "+ 0.00000000" not in panel.text  # no orders, not shown (D-67)

    async def test_read_only_no_panel(self, tmp_path):
        feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        app = local_app(MarketHub(feeds), book_levels=15, timeframe="1m", mode="read-only")
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            assert not app.query_one(BalancesPanel).display

    async def test_fits_with_everything_at_160x45(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            panel = rig.app.query_one(BalancesPanel)
            await until(pilot, lambda: "valor" in panel.text)
            assert all(len(ln) <= 156 for ln in panel.text.splitlines())
            assert all(chart.region.height >= 8 for chart in rig.app.query(ChartPanel))


@pytest.mark.spec("RF-08")
class TestOpenOrders:
    async def test_o_opens_list(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            first_order = await place(rig, "nk")
            second_order = await place(rig, "nx", side="sell", price="0.00730000")
            await until(pilot, lambda: total_open(rig) == 2)
            await pilot.press("o")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            screen = rig.app.screen
            assert screen.order_ids == [first_order.order_id, second_order.order_id]
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(rig.app.screen, OrdersScreen))

    async def test_orders_of_a_venue(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await place(rig, "nk")
            second_order = await place(rig, "nx", side="sell", price="0.00730000")
            await until(pilot, lambda: total_open(rig) == 2)
            await command(rig, pilot, "orders nx")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            assert rig.app.screen.order_ids == [second_order.order_id]

    async def test_cancel_selected_with_confirmation(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            first_order = await place(rig, "nk")
            await until(pilot, lambda: total_open(rig) == 1)
            await pilot.press("o")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            await pilot.press("c")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert first_order.order_id in rig.app.screen.summary
            await pilot.press("y")
            await until(pilot, lambda: total_open(rig) == 0, timeout=3)
            assert "cancelado" in rig.app.last_message
            assert await rig.venues["nk"].open_orders() == []
            assert rig.events()[-2:] == ["cancel_sent", "cancel_ack"]

    async def test_list_updates_by_itself(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await pilot.press("o")
            await until(pilot, lambda: isinstance(rig.app.screen, OrdersScreen))
            assert rig.app.screen.order_ids == []
            first_order = await place(rig, "nk")
            await until(
                pilot, lambda: rig.app.screen.order_ids == [first_order.order_id], timeout=3
            )


@pytest.mark.spec("RF-08", "CA-05")
class TestCancelAll:
    async def test_x_cancels_all_after_confirming(self, tmp_path):
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
    async def test_does_not_cancel_by_default(self, tmp_path, key):
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

    async def test_cancel_commands(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            first_order = await place(rig, "nk")
            await place(rig, "nx", side="sell", price="0.00730000")
            await command(rig, pilot, f"cancel {first_order.order_id}")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            await pilot.press("y")
            await until(pilot, lambda: "cancelado" in rig.app.last_message, timeout=3)
            await command(rig, pilot, "cancel nx")
            await until(pilot, lambda: isinstance(rig.app.screen, ConfirmScreen))
            assert "en Neoxa" in rig.app.screen.summary
            await pilot.press("y")
            await until(pilot, lambda: "cancelado" in rig.app.last_message, timeout=3)
            assert [len(await venue.open_orders()) for venue in rig.venues.values()] == [0, 0]

    async def test_no_orders_says_so_without_dialog(self, tmp_path):
        rig = Rig(tmp_path)
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await pilot.press("x")
            await until(pilot, lambda: "no hay órdenes abiertas" in rig.app.last_message)
            assert not isinstance(rig.app.screen, ConfirmScreen)


@pytest.mark.spec("RF-07")
async def test_d_starts_a_double_order(tmp_path):
    rig = Rig(tmp_path)
    async with rig.app.run_test(size=(160, 45)) as pilot:
        await pilot.press("d")
        command = rig.app.query_one(CommandLine)
        await until(pilot, lambda: command.has_focus and command.value == "dbl ")


@pytest.mark.spec("RF-06", "RF-08")
async def test_bracketed_keys_are_visible(tmp_path):
    """Without markup=False, Rich eats "[y]" and the dialog does not say how to confirm."""
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
async def test_bracketed_message_shown_as_is(tmp_path, text):
    """Venue errors may bring brackets: with markup, "[red]" vanishes and "[/bold]" raises
    MarkupError when drawing the notice."""
    from textual.content import Content

    rig = Rig(tmp_path)
    async with rig.app.run_test(size=(160, 45)) as pilot:
        rig.app.say(text, error=True)
        await pilot.pause()
        (note,) = list(rig.app._notifications)
        # the same as Toast.render does
        shown = Content.from_markup(note.message) if note.markup else Content(note.message)
        assert shown.plain == text
