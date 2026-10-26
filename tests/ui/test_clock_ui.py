"""Aviso de reloj desfasado al cargar cada venue (T-29, RNF-16, D-30)."""

import pytest

from .test_orders_ui import Rig, ready, until


@pytest.mark.spec("RNF-16")
class TestAvisoDeDesfase:
    async def test_dry_run_avisa_una_vez(self, tmp_path):
        rig = Rig(tmp_path, max_clock_skew_s=5)
        rig.feeds["nk"].clock_skew_ms = -61_000
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await until(pilot, lambda: "reloj" in rig.app.last_message)
            msg = rig.app.last_message
            assert rig.app.last_error and "NonKYC" in msg and "-61 s" in msg
            assert "en live no se podría operar" in msg and "NTP" in msg
            await ready(rig, pilot)
            rig.app.say("otra cosa")
            rig.hub._notify("nk", "market")  # otra carga: no se repite
            await pilot.pause(0.05)
            assert rig.app.last_message == "otra cosa"

    async def test_live_dice_que_no_se_envian_ordenes(self, tmp_path):
        rig = Rig(tmp_path, max_clock_skew_s=5)
        rig.app.mode = rig.app.engine.mode = "live"  # el aviso sale del motor (D-61)
        rig.feeds["nk"].clock_skew_ms = 9_000
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await until(pilot, lambda: "reloj" in rig.app.last_message)
            assert "no se envían órdenes a NonKYC" in rig.app.last_message

    async def test_dentro_del_tope_no_avisa(self, tmp_path):
        rig = Rig(tmp_path, max_clock_skew_s=5)
        rig.feeds["nk"].clock_skew_ms = 4_000
        async with rig.app.run_test(size=(160, 45)) as pilot:
            await ready(rig, pilot)
            await pilot.pause(0.05)
            assert "reloj" not in rig.app.last_message


@pytest.mark.spec("RNF-18")
async def test_avisos_de_arranque_en_rojo(tmp_path):
    rig = Rig(tmp_path, startup_warnings=(".env legible por otros: chmod 600 .env",))
    async with rig.app.run_test(size=(160, 45)) as pilot:
        await until(pilot, lambda: "chmod 600" in rig.app.last_message)
        assert rig.app.last_error
