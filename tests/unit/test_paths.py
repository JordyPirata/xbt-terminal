import os
import stat
from pathlib import Path

import pytest

from btcb2_terminal.config import ConfigError, load_settings
from btcb2_terminal.paths import (
    AlreadyRunning,
    InstanceLock,
    env_permission_problem,
    private_dir,
    resolve_paths,
)

EXAMPLE = Path(__file__).parents[2] / "config.example.toml"


def env(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("NONKYC_API_KEY=k_CANARIO\nNONKYC_API_SECRET=s_CANARIO\n")
    return path


def mode_of(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.spec("RNF-20")
def test_rutas_xdg_por_defecto(tmp_path):
    environ = {"XDG_CONFIG_HOME": str(tmp_path / "cfg"), "XDG_STATE_HOME": str(tmp_path / "st")}
    paths = resolve_paths(environ=environ)
    assert paths.config == tmp_path / "cfg" / "btcb2t" / "config.toml"
    assert paths.env == tmp_path / "cfg" / "btcb2t" / ".env"
    assert paths.data == tmp_path / "st" / "btcb2t"


@pytest.mark.spec("RNF-20")
def test_sin_xdg_cuelga_de_home(tmp_path):
    paths = resolve_paths(environ={"HOME": str(tmp_path)})
    assert paths.config == tmp_path / ".config" / "btcb2t" / "config.toml"
    assert paths.data == tmp_path / ".local" / "state" / "btcb2t"


@pytest.mark.spec("RNF-20")
def test_xdg_relativo_se_ignora(tmp_path):
    """La especificación XDG pide ignorar rutas relativas: dependerían del cwd."""
    paths = resolve_paths(environ={"HOME": str(tmp_path), "XDG_STATE_HOME": "rel/state"})
    assert paths.data == tmp_path / ".local" / "state" / "btcb2t"


@pytest.mark.spec("RNF-20")
def test_los_flags_mandan_y_salen_absolutos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = resolve_paths(
        config="mi.toml", env="secretos.env", data_dir="datos", environ={"HOME": "/nadie"}
    )
    assert paths.config == tmp_path / "mi.toml"
    assert paths.env == tmp_path / "secretos.env"
    assert paths.data == tmp_path / "datos"
    assert all(p.is_absolute() for p in (paths.config, paths.env, paths.data))


@pytest.mark.spec("RNF-18")
def test_directorio_de_datos_0700(tmp_path):
    data = tmp_path / "a" / "datos"
    private_dir(data)
    assert mode_of(data) == 0o700
    data.chmod(0o755)
    private_dir(data)  # uno ya creado con más permisos se cierra
    assert mode_of(data) == 0o700


@pytest.mark.spec("RNF-17")
def test_segunda_instancia_rechazada(tmp_path):
    first = InstanceLock(tmp_path)
    first.acquire()
    with pytest.raises(AlreadyRunning, match=str(os.getpid())) as exc:
        InstanceLock(tmp_path).acquire()
    assert str(tmp_path) in str(exc.value)
    first.release()
    again = InstanceLock(tmp_path)
    again.acquire()  # al soltarla, otra puede arrancar
    again.release()


@pytest.mark.spec("RNF-17", "RNF-18")
def test_el_lock_crea_el_directorio_privado(tmp_path):
    data = tmp_path / "nuevo"
    with InstanceLock(data):
        assert mode_of(data) == 0o700
        assert mode_of(data / "btcb2t.lock") == 0o600


@pytest.mark.spec("RNF-18")
def test_permisos_del_env(tmp_path):
    path = env(tmp_path)
    path.chmod(0o600)
    assert env_permission_problem(path) is None
    for loose in (0o640, 0o604, 0o620):
        path.chmod(loose)
        assert "chmod 600" in env_permission_problem(path)
    assert env_permission_problem(tmp_path / "no-existe.env") is None


@pytest.mark.spec("RNF-18")
def test_env_abierto_rechaza_live(tmp_path):
    path = env(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_settings(EXAMPLE, path, live=True)


@pytest.mark.spec("RNF-18")
def test_env_abierto_avisa_en_dry_run(tmp_path):
    path = env(tmp_path)
    path.chmod(0o644)
    settings = load_settings(EXAMPLE, path, live=False)
    assert settings.mode == "dry-run"
    assert any("chmod 600" in w for w in settings.warnings)
    path.chmod(0o600)
    assert load_settings(EXAMPLE, path, live=True).warnings == ()
