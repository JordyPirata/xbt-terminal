"""Diagnostic log (T-31, RNF-19): rotating, redacted and never on screen."""

import logging
import stat

import pytest

from xbt_terminal.config import ConfigError, load_config
from xbt_terminal.core.secrets import Redactor, Secret
from xbt_terminal.diaglog import LOG_FILE, setup_diagnostics

from .test_config import EXAMPLE, example_with

CANARY = "nk_sec_CANARIO_31"
log = logging.getLogger("xbt_terminal.prueba")


@pytest.fixture
def diag(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([Secret(CANARY)]), "info")
    yield tmp_path / LOG_FILE
    handler.close()


@pytest.mark.spec("RNF-19", "CA-07")
def test_key_does_not_reach_the_log(diag):
    log.warning("respuesta rara de NonKYC: %s", f"auth={CANARY}")
    try:
        raise RuntimeError(f"firma con {CANARY}")
    except RuntimeError:
        log.exception("fallo al firmar")
    text = diag.read_text()
    assert CANARY not in text
    assert "auth=***" in text and "firma con ***" in text
    assert "Traceback" in text  # the traceback is there, redacted


@pytest.mark.spec("RNF-19")
def test_format_with_time_level_and_module(diag):
    log.warning("ws down")
    (line,) = diag.read_text().splitlines()
    assert "WARNING" in line and "xbt_terminal.prueba" in line and line.endswith("ws down")
    assert line[:4].isdigit()  # starts with the date


@pytest.mark.spec("RNF-19", "RNF-18")
def test_file_for_user_only(diag):
    log.warning("algo")
    assert stat.S_IMODE(diag.stat().st_mode) == 0o600


@pytest.mark.spec("RNF-19")
def test_config_level(diag):
    log.debug("detalle")
    log.info("carga")
    assert "detalle" not in diag.read_text() and "carga" in diag.read_text()


@pytest.mark.spec("RNF-19")
def test_nothing_on_screen(diag, capfd):
    log.error("esto solo va al fichero")
    out, err = capfd.readouterr()
    assert out == "" and err == ""


@pytest.mark.spec("RNF-19")
def test_unconfigured_does_not_write_on_screen_either(capfd):
    """Without a handler, logging would use `lastResort` and write to stderr over the TUI."""
    log.error("nobody set up the log")
    out, err = capfd.readouterr()
    assert out == "" and err == ""


@pytest.mark.spec("RNF-19")
def test_rotates_by_size(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([]), "info", max_bytes=200, backups=2)
    try:
        for index in range(40):
            log.warning("filler message number %d", index)
    finally:
        handler.close()
    names = sorted(config_path.name for config_path in tmp_path.iterdir())
    assert names == [LOG_FILE, f"{LOG_FILE}.1", f"{LOG_FILE}.2"]
    assert all(stat.S_IMODE((tmp_path / index).stat().st_mode) == 0o600 for index in names)
    assert "number 39" in (tmp_path / LOG_FILE).read_text()


@pytest.mark.spec("RNF-19")
def test_close_removes_the_handler(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([]), "info")
    handler.close()
    log.warning("after closing")
    assert "after" not in (tmp_path / LOG_FILE).read_text()


@pytest.mark.spec("RNF-19")
class TestConfig:
    def test_defaults_to_info(self):
        assert load_config(EXAMPLE).log.level == "info"

    def test_no_section_is_info(self, tmp_path):
        config_path = example_with(tmp_path, '[log]\nlevel = "info"', "")
        assert load_config(config_path).log.level == "info"

    def test_unknown_level(self, tmp_path):
        config_path = example_with(tmp_path, 'level = "info"', 'level = "verbose"')
        with pytest.raises(ConfigError, match=r"log\.level"):
            load_config(config_path)
