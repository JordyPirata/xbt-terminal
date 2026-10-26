"""Texto de un error (T-43, D-51): nunca vacío. `ConnectTimeout` y compañía de
httpx llegan con `str(exc) == ""` y dejaban errores en blanco en la cuenta, el
journal y el motivo de paro del bot."""

import httpx
import pytest

from btcb2_terminal.adapters.base import VenueError, describe


@pytest.mark.spec("RF-11")
class TestDescribe:
    def test_con_texto_es_el_texto(self):
        assert describe(VenueError("Neoxa: HTTP 500")) == "Neoxa: HTTP 500"
        assert describe(httpx.ConnectError("[Errno -2] Name or service not known")) == (
            "[Errno -2] Name or service not known"
        )

    @pytest.mark.parametrize(
        "exc", [httpx.ConnectTimeout(""), httpx.ReadTimeout(""), TimeoutError()]
    )
    def test_sin_texto_es_el_tipo(self, exc):
        assert describe(exc) == type(exc).__name__

    def test_solo_espacios_cuenta_como_vacio(self):
        assert describe(VenueError("  ")) == "VenueError"
