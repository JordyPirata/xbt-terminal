"""Diagnostic log (T-31, T-112, RNF-19): the daemon's on stderr for journald, the
screen's in a rotating file; both redacted."""

import logging
import re
import stat
from contextlib import contextmanager

import pytest

from xbt_terminal.config import ConfigError, load_config
from xbt_terminal.core.secrets import Redactor, Secret
from xbt_terminal.diaglog import CLIENT_LOG, setup_daemon_diagnostics, setup_diagnostics

from .test_config import EXAMPLE, example_with

CANARY = "nk_sec_CANARIO_31"
log = logging.getLogger("xbt_terminal.prueba")
# logback's `%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level %logger - %msg`, in UTC
LINE = re.compile(
    r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}Z (DEBUG|INFO |WARN |ERROR|FATAL) \S+ - .*"
)


@pytest.fixture
def diag(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([Secret(CANARY)]), "info")
    yield tmp_path / CLIENT_LOG
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
    assert LINE.fullmatch(line) and " WARN  xbt_terminal.prueba - ws down" in line


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
    assert names == [CLIENT_LOG, f"{CLIENT_LOG}.1", f"{CLIENT_LOG}.2"]
    assert all(stat.S_IMODE((tmp_path / index).stat().st_mode) == 0o600 for index in names)
    assert "number 39" in (tmp_path / CLIENT_LOG).read_text()


@pytest.mark.spec("RNF-19")
def test_close_removes_the_handler(tmp_path):
    handler = setup_diagnostics(tmp_path, Redactor([]), "info")
    handler.close()
    log.warning("after closing")
    assert "after" not in (tmp_path / CLIENT_LOG).read_text()


@contextmanager
def daemon_log():
    """The daemon's log outside systemd. Opened in the test body: pytest swaps
    `sys.stderr` between setup and call, and the handler keeps the one it was given."""
    handler = setup_daemon_diagnostics(Redactor([Secret(CANARY)]), "info")
    try:
        yield
    finally:
        handler.close()


@pytest.fixture(autouse=True)
def outside_systemd(monkeypatch):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)


@pytest.mark.spec("RNF-19", "CA-07")
def test_daemon_key_does_not_reach_stderr(capfd):
    with daemon_log():
        log.warning("respuesta rara de NonKYC: %s", f"auth={CANARY}")
        try:
            raise RuntimeError(f"firma con {CANARY}")
        except RuntimeError:
            log.exception("fallo al firmar")
        out, err = capfd.readouterr()
        assert out == "" and CANARY not in err
        assert "auth=***" in err and "firma con ***" in err and "Traceback" in err


@pytest.mark.spec("RNF-19")
def test_daemon_writes_no_file(capfd, tmp_path, monkeypatch):
    with daemon_log():
        monkeypatch.chdir(tmp_path)
        log.warning("ws down")
        assert list(tmp_path.iterdir()) == []
        assert capfd.readouterr().err.endswith("ws down\n")


@pytest.mark.spec("RNF-19")
def test_daemon_line_outside_systemd_has_time_level_and_module(capfd):
    with daemon_log():
        log.warning("ws down")
        (line,) = capfd.readouterr().err.splitlines()
        assert LINE.fullmatch(line) and " WARN  xbt_terminal.prueba - ws down" in line


@pytest.mark.spec("RNF-19")
def test_daemon_config_level(capfd):
    with daemon_log():
        log.debug("detalle")
        log.info("carga")
        err = capfd.readouterr().err
        assert "detalle" not in err and "carga" in err


@pytest.mark.spec("RNF-19")
def test_daemon_under_journald_carries_priority_and_the_same_line(monkeypatch, capfd):
    monkeypatch.setenv("JOURNAL_STREAM", "8:12345")
    handler = setup_daemon_diagnostics(Redactor([]), "debug")
    try:
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.error("e")
        log.critical("c")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("x")
    finally:
        handler.close()
    lines = capfd.readouterr().err.splitlines()
    heads = [(line[:3], line[3:].split("Z ", 1)[1]) for line in lines[:6]]
    assert heads == [
        ("<7>", "DEBUG xbt_terminal.prueba - d"),
        ("<6>", "INFO  xbt_terminal.prueba - i"),
        ("<4>", "WARN  xbt_terminal.prueba - w"),
        ("<3>", "ERROR xbt_terminal.prueba - e"),
        ("<2>", "FATAL xbt_terminal.prueba - c"),
        ("<3>", "ERROR xbt_terminal.prueba - x"),
    ]
    assert all(LINE.fullmatch(line[3:]) for line in lines[:6])
    assert "Traceback" in "\n".join(lines[6:])


@pytest.mark.spec("RNF-19")
def test_daemon_close_removes_the_handler(capfd):
    handler = setup_daemon_diagnostics(Redactor([]), "info")
    handler.close()
    log.warning("after closing")
    assert capfd.readouterr().err == ""
    assert handler not in logging.getLogger("xbt_terminal").handlers


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
