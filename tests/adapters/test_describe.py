"""Text of an error (T-43, D-51): never empty. httpx's `ConnectTimeout` and friends come
with `str(exc) == ""` and left blank errors in the account, the journal and the
bot's stop reason."""

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError, describe


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
