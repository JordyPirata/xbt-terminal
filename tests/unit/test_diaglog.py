"""Log de diagnóstico (T-31, RNF-19): rotativo, redactado y nunca en la pantalla."""

import logging
import stat

import pytest

from btcb2_terminal.config import ConfigError, load_config
from btcb2_terminal.core.secrets import Redactor, Secret
from btcb2_terminal.diaglog import LOG_FILE, setup_diagnostics

from .test_config import EXAMPLE, example_with

CANARY = "nk_sec_CANARIO_31"
log = logging.getLogger("btcb2_terminal.prueba")


@pytest.fixture
def diag(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([Secret(CANARY)]), "info")
    yield tmp_path / LOG_FILE
    handler.close()


@pytest.mark.spec("RNF-19", "CA-07")
def test_la_key_no_llega_al_log(diag):
    log.warning("respuesta rara de NonKYC: %s", f"auth={CANARY}")
    try:
        raise RuntimeError(f"firma con {CANARY}")
    except RuntimeError:
        log.exception("fallo al firmar")
    text = diag.read_text()
    assert CANARY not in text
    assert "auth=***" in text and "firma con ***" in text
    assert "Traceback" in text  # el traceback va, redactado


@pytest.mark.spec("RNF-19")
def test_formato_con_hora_nivel_y_modulo(diag):
    log.warning("ws caído")
    (line,) = diag.read_text().splitlines()
    assert "WARNING" in line and "btcb2_terminal.prueba" in line and line.endswith("ws caído")
    assert line[:4].isdigit()  # empieza por la fecha


@pytest.mark.spec("RNF-19", "RNF-18")
def test_fichero_solo_para_el_usuario(diag):
    log.warning("algo")
    assert stat.S_IMODE(diag.stat().st_mode) == 0o600


@pytest.mark.spec("RNF-19")
def test_nivel_de_la_config(diag):
    log.debug("detalle")
    log.info("carga")
    assert "detalle" not in diag.read_text() and "carga" in diag.read_text()


@pytest.mark.spec("RNF-19")
def test_nada_en_la_pantalla(diag, capfd):
    log.error("esto solo va al fichero")
    out, err = capfd.readouterr()
    assert out == "" and err == ""


@pytest.mark.spec("RNF-19")
def test_sin_configurar_tampoco_escribe_en_la_pantalla(capfd):
    """Sin handler, logging usaría `lastResort` y escribiría en stderr encima de la TUI."""
    log.error("nadie configuró el log")
    out, err = capfd.readouterr()
    assert out == "" and err == ""


@pytest.mark.spec("RNF-19")
def test_rota_por_tamano(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([]), "info", max_bytes=200, backups=2)
    try:
        for n in range(40):
            log.warning("mensaje de relleno número %d", n)
    finally:
        handler.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [LOG_FILE, f"{LOG_FILE}.1", f"{LOG_FILE}.2"]
    assert all(stat.S_IMODE((tmp_path / n).stat().st_mode) == 0o600 for n in names)
    assert "número 39" in (tmp_path / LOG_FILE).read_text()


@pytest.mark.spec("RNF-19")
def test_cerrar_quita_el_handler(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([]), "info")
    handler.close()
    log.warning("después de cerrar")
    assert "después" not in (tmp_path / LOG_FILE).read_text()


@pytest.mark.spec("RNF-19")
class TestConfig:
    def test_por_defecto_info(self):
        assert load_config(EXAMPLE).log.level == "info"

    def test_sin_seccion_es_info(self, tmp_path):
        p = example_with(tmp_path, '[log]\nlevel = "info"', "")
        assert load_config(p).log.level == "info"

    def test_nivel_desconocido(self, tmp_path):
        p = example_with(tmp_path, 'level = "info"', 'level = "verbose"')
        with pytest.raises(ConfigError, match=r"log\.level"):
            load_config(p)
