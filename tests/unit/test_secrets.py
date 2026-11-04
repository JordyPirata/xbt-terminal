import logging
import pickle

import pytest

from xbt_terminal.core.secrets import Redactor, Secret

CANARY = "nex_CANARIO_9f3a1c77d2"


@pytest.mark.spec("RF-12", "CA-07")
class TestSecretNotPrinted:
    def test_str_repr_and_fstring(self):
        s = Secret(CANARY)
        for rendered in (str(s), repr(s), f"{s}", f"{s!r}", "%s" % s, "{}".format(s)):  # noqa: UP031, UP032
            assert CANARY not in rendered

    def test_inside_containers(self):
        s = Secret(CANARY)
        assert CANARY not in repr({"key": s}) + repr([s]) + repr((s,))

    def test_into_logging(self, caplog):
        with caplog.at_level(logging.INFO):
            logging.getLogger("t").info("key=%s", Secret(CANARY))
        assert CANARY not in caplog.text

    def test_not_serializable(self):
        with pytest.raises(TypeError):
            pickle.dumps(Secret(CANARY))

    def test_reveal_returns_the_value(self):
        assert Secret(CANARY).reveal() == CANARY

    def test_empty_is_no_secret(self):
        with pytest.raises(ValueError):
            Secret("")


@pytest.mark.spec("RF-12", "CA-07")
class TestRedactor:
    def test_replaces_each_occurrence(self):
        r = Redactor([Secret(CANARY), Secret("otro_secreto_123")])
        out = r.redact(f'{{"err": "401 key={CANARY}", "s": "otro_secreto_123", "k": "{CANARY}"}}')
        assert CANARY not in out and "otro_secreto_123" not in out
        assert out.count("***") == 3

    def test_no_secrets_changes_nothing(self):
        assert Redactor([]).redact("hola") == "hola"

    def test_secret_inside_another(self):
        # longest first: otherwise a piece of the long one would stay exposed
        r = Redactor([Secret("abc123"), Secret("abc123XYZ")])
        assert r.redact("abc123XYZ") == "***"
