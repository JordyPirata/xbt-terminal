"""Copiar con la herramienta del sistema (T-44, RF-28, D-52): GNOME Terminal
ignora OSC 52, así que se prueba `wl-copy`, `xclip` o `xsel`."""

import pytest

from xbt_terminal.ui.clipboard import system_copy

ADDRESS = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"


class Runner:
    def __init__(self, codes=None):
        self.codes = codes or {}
        self.calls: list[tuple[list[str], bytes]] = []

    async def __call__(self, argv, data):
        self.calls.append((argv, data))
        return self.codes.get(argv[0], 0)


def which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


@pytest.mark.spec("RF-28", "CA-13")
class TestSystemCopy:
    async def test_x11_usa_xclip_con_el_texto_exacto(self):
        run = Runner()
        got = await system_copy(ADDRESS, env={"DISPLAY": ":0"}, which=which("xclip"), run=run)
        assert got == "xclip"
        assert run.calls == [(["/usr/bin/xclip", "-selection", "clipboard"], ADDRESS.encode())]

    async def test_wayland_primero_wl_copy(self):
        run = Runner()
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        got = await system_copy(ADDRESS, env=env, which=which("wl-copy", "xclip"), run=run)
        assert got == "wl-copy" and len(run.calls) == 1

    async def test_si_una_falla_prueba_la_siguiente(self):
        run = Runner(codes={"/usr/bin/xclip": 1})
        got = await system_copy(
            ADDRESS, env={"DISPLAY": ":0"}, which=which("xclip", "xsel"), run=run
        )
        assert got == "xsel"
        assert run.calls[1] == (["/usr/bin/xsel", "--clipboard", "--input"], ADDRESS.encode())

    async def test_una_que_revienta_no_rompe(self):
        async def boom(argv, data):
            raise OSError("no se puede ejecutar")

        got = await system_copy(ADDRESS, env={"DISPLAY": ":0"}, which=which("xclip"), run=boom)
        assert got is None

    async def test_sin_herramientas_o_sin_pantalla_da_none(self):
        run = Runner()
        assert await system_copy(ADDRESS, env={"DISPLAY": ":0"}, which=which(), run=run) is None
        # xclip instalado pero sin sesión gráfica (ssh): ni se intenta
        assert await system_copy(ADDRESS, env={}, which=which("xclip"), run=run) is None
        assert run.calls == []

    async def test_el_runner_real_no_espera_al_proceso_en_segundo_plano(self, tmp_path):
        # xclip se queda vivo sirviendo la selección: con la salida en un pipe la
        # espera no acabaría. Un `sh` que deja un hijo dormido lo imita.
        script = tmp_path / "xclip"
        script.write_text("#!/bin/sh\ncat > /dev/null\nsleep 30 &\nexit 0\n")
        script.chmod(0o755)
        got = await system_copy(
            ADDRESS, env={"DISPLAY": ":0"}, which=lambda n: str(script) if n == "xclip" else None
        )
        assert got == "xclip"
