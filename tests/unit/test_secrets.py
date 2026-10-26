import logging
import pickle

import pytest

from btcb2_terminal.core.secrets import Redactor, Secret

CANARY = "nex_CANARIO_9f3a1c77d2"


@pytest.mark.spec("RF-12", "CA-07")
class TestSecretNoSeImprime:
    def test_str_repr_y_fstring(self):
        s = Secret(CANARY)
        for rendered in (str(s), repr(s), f"{s}", f"{s!r}", "%s" % s, "{}".format(s)):  # noqa: UP031, UP032
            assert CANARY not in rendered

    def test_dentro_de_contenedores(self):
        s = Secret(CANARY)
        assert CANARY not in repr({"key": s}) + repr([s]) + repr((s,))

    def test_en_logging(self, caplog):
        with caplog.at_level(logging.INFO):
            logging.getLogger("t").info("key=%s", Secret(CANARY))
        assert CANARY not in caplog.text

    def test_no_se_serializa(self):
        with pytest.raises(TypeError):
            pickle.dumps(Secret(CANARY))

    def test_reveal_devuelve_el_valor(self):
        assert Secret(CANARY).reveal() == CANARY

    def test_vacio_no_es_secreto(self):
        with pytest.raises(ValueError):
            Secret("")


@pytest.mark.spec("RF-12", "CA-07")
class TestRedactor:
    def test_reemplaza_cada_aparicion(self):
        r = Redactor([Secret(CANARY), Secret("otro_secreto_123")])
        out = r.redact(f'{{"err": "401 key={CANARY}", "s": "otro_secreto_123", "k": "{CANARY}"}}')
        assert CANARY not in out and "otro_secreto_123" not in out
        assert out.count("***") == 3

    def test_sin_secretos_no_cambia_nada(self):
        assert Redactor([]).redact("hola") == "hola"

    def test_secreto_contenido_en_otro(self):
        # el más largo primero: si no, quedaría un trozo del largo al descubierto
        r = Redactor([Secret("abc123"), Secret("abc123XYZ")])
        assert r.redact("abc123XYZ") == "***"
