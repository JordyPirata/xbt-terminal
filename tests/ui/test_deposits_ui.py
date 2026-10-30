"""Ventana de direcciones de depósito (T-42, RF-28, CA-13): tecla `w`, comando
`deposit`, filas con venue, ticker y red, errores sin dirección, recarga al
reabrir y copia exacta."""

import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.services.deposits import DepositService
from xbt_terminal.services.market import MarketHub
from xbt_terminal.ui.qr import qr_lines
from xbt_terminal.ui.screens.deposit import DepositScreen

from ..fakes import FakeFeed
from ..services.test_deposits_service import FakeDepositor
from .local import local_app
from .test_orders_ui import Rig, type_command, until


def make_app(deposits, clipboard=None):
    feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
    return local_app(
        MarketHub(feeds),
        book_levels=15,
        timeframe="1m",
        mode="read-only",
        deposits=deposits,
        clipboard=clipboard,
    )


class FakeClipboard:
    def __init__(self, tool="xclip"):
        self.tool = tool
        self.copied: list[str] = []

    async def __call__(self, text):
        self.copied.append(text)
        return self.tool


def screen(app) -> DepositScreen:
    assert isinstance(app.screen, DepositScreen)
    return app.screen


async def opened(app, pilot):
    await until(pilot, lambda: isinstance(app.screen, DepositScreen))
    await until(pilot, lambda: screen(app).rows is not None)
    return screen(app)


@pytest.mark.spec("RF-28", "CA-13")
class TestPantalla:
    async def test_tecla_w_muestra_las_direcciones_y_esc_cierra(self):
        nx = FakeDepositor("nx", fail={"BTCB2": VenueError("Neoxa: btcb2 cerrada")})
        app = make_app(DepositService({"nk": FakeDepositor("nk"), "nx": nx}))
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            s = await opened(app, pilot)
            lines = s.table_text().splitlines()
            assert len(lines) == 4
            nk_base = next(ln for ln in lines if "nk-BTCB2" in ln)
            # nombre mostrado y ticker del venue, para no equivocar el activo
            assert "XBT (BTCB2)" in nk_base and "NonKYC" in nk_base and "red" in nk_base
            failed = next(ln for ln in lines if "Neoxa" in ln and "XBT" in ln)
            assert "btcb2 cerrada" in failed and "nx-BTCB2" not in failed
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, DepositScreen))

    async def test_comando_deposit(self, tmp_path):
        # en dry-run: en solo lectura la línea de comandos está apagada
        app = Rig(tmp_path, deposits=DepositService({"nk": FakeDepositor("nk")})).app
        async with app.run_test(size=(160, 45)) as pilot:
            await type_command(pilot, app, "deposit")
            s = await opened(app, pilot)
            assert len(s.table_text().splitlines()) == 2

    async def test_reabrir_vuelve_a_pedir(self):
        nk = FakeDepositor("nk")
        app = make_app(DepositService({"nk": nk}))
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            await opened(app, pilot)
            await pilot.press("escape")
            await until(pilot, lambda: not isinstance(app.screen, DepositScreen))
            await pilot.press("w")
            await opened(app, pilot)
            assert nk.calls == ["BTC", "BTCB2", "BTC", "BTCB2"]

    async def test_r_recarga(self):
        nk = FakeDepositor("nk")
        app = make_app(DepositService({"nk": nk}))
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            await opened(app, pilot)
            await pilot.press("r")
            await until(pilot, lambda: len(nk.calls) == 4)

    async def test_copiar_la_direccion_exacta(self, monkeypatch):
        app = make_app(DepositService({"nk": FakeDepositor("nk")}))
        copied = []
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            s = await opened(app, pilot)
            await pilot.press("down")  # segunda fila: BTCB2 de NonKYC
            await pilot.press("c")
            await until(pilot, lambda: copied == ["nk-BTCB2"])
            await until(pilot, lambda: "nk-BTCB2" in s.status_text())
            await pilot.press("up", "enter")
            await until(pilot, lambda: copied == ["nk-BTCB2", "nk-BTC"])

    async def test_copiar_una_fila_con_error_no_copia(self, monkeypatch):
        nk = FakeDepositor("nk", fail={"BTC": VenueError("caída")})
        app = make_app(DepositService({"nk": nk}))
        copied = []
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            s = await opened(app, pilot)
            await pilot.press("c")
            assert copied == [] and "sin dirección" in s.status_text()

    async def test_copia_con_el_sistema_y_lo_dice(self, monkeypatch):
        clip = FakeClipboard("xclip")
        app = make_app(DepositService({"nk": FakeDepositor("nk")}), clipboard=clip)
        osc: list[str] = []
        monkeypatch.setattr(app, "copy_to_clipboard", osc.append)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            s = await opened(app, pilot)
            await pilot.press("c")
            await until(pilot, lambda: "xclip" in s.status_text())
            assert clip.copied == ["nk-BTC"] and osc == ["nk-BTC"]
            assert "copiada" in s.status_text()

    async def test_sin_herramienta_dice_que_solo_fue_a_la_terminal(self, monkeypatch):
        app = make_app(DepositService({"nk": FakeDepositor("nk")}), clipboard=FakeClipboard(None))
        monkeypatch.setattr(app, "copy_to_clipboard", lambda text: None)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            s = await opened(app, pilot)
            await pilot.press("c")
            await until(pilot, lambda: "terminal" in s.status_text())
            assert "copiada" not in s.status_text()
            assert "Shift" in s.status_text() and "nk-BTC" in s.status_text()

    async def test_el_qr_sigue_al_cursor(self):
        app = make_app(DepositService({"nk": FakeDepositor("nk")}))
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            s = await opened(app, pilot)
            await until(pilot, lambda: s.qr_text() == "\n".join(qr_lines("nk-BTC")))
            await pilot.press("down")
            await until(pilot, lambda: s.qr_text() == "\n".join(qr_lines("nk-BTCB2")))

    async def test_fila_con_error_sin_qr(self):
        nk = FakeDepositor("nk", fail={"BTC": VenueError("caída")})
        app = make_app(DepositService({"nk": nk}))
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            s = await opened(app, pilot)
            await until(pilot, lambda: "sin QR" in s.qr_text())
            assert "█" not in s.qr_text()

    async def test_sin_keys_lo_dice(self):
        app = make_app(None)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("w")
            await pilot.pause()
            assert not isinstance(app.screen, DepositScreen)
            assert app.last_error and "keys" in app.last_message
