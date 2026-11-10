"""Text of an error (T-43, D-51): never empty. httpx's `ConnectTimeout` and friends come
with `str(exc) == ""` and left blank errors in the account, the journal and the
bot's stop reason."""

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError, describe


@pytest.mark.spec("RF-11")
class TestDescribe:
    def test_with_text_is_the_text(self):
        assert describe(VenueError("Neoxa: HTTP 500")) == "Neoxa: HTTP 500"
        assert describe(httpx.ConnectError("[Errno -2] Name or service not known")) == (
            "[Errno -2] Name or service not known"
        )

    @pytest.mark.parametrize(
        "error", [httpx.ConnectTimeout(""), httpx.ReadTimeout(""), TimeoutError()]
    )
    def test_no_text_is_the_type(self, error):
        assert describe(error) == type(error).__name__

    def test_only_spaces_counts_as_empty(self):
        assert describe(VenueError("  ")) == "VenueError"
