"""`[dryrun.balances]` is gone: dry-run uses the real balances (D-16)."""

import pytest

from xbt_terminal.config import ConfigError, load_config, load_settings
from xbt_terminal.core.models import D

from .test_config import EXAMPLE, write

OLD = '\n[dryrun.balances.nk]\nBTC   = "0.005"\nBTCB2 = "0.5"\n'


@pytest.mark.spec("RF-13")
class TestSinSaldosSimulados:
    def test_el_ejemplo_no_trae_la_seccion(self):
        assert "dryrun" not in EXAMPLE.read_text(encoding="utf-8")
        assert not hasattr(load_config(EXAMPLE), "dryrun_balances")

    def test_una_config_vieja_explica_que_hacer(self, tmp_path):
        path = write(tmp_path, EXAMPLE.read_text(encoding="utf-8") + OLD)
        with pytest.raises(ConfigError, match=r"\[dryrun\].*saldos reales.*bórrala"):
            load_config(path)


REPLAY = '\n[replay]\nbase_balance = "10"\nquote_balance = "0.5"\n'


@pytest.mark.spec("RF-34")
class TestReplay:
    def write(self, tmp_path, extra=REPLAY):
        path = tmp_path / "config.toml"
        path.write_text(EXAMPLE.read_text() + extra)
        return path

    def test_sin_keys_es_dry_run_y_puede_operar(self, tmp_path):
        settings = load_settings(self.write(tmp_path), tmp_path / "no.env", False, replay=True)
        assert settings.mode == "dry-run" and settings.replay
        assert settings.can_trade("nk") and settings.can_trade("nx")

    def test_ni_siquiera_lee_el_env(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("NONKYC_API_KEY=solo-una\n")  # reading it would be an error
        settings = load_settings(self.write(tmp_path), env, False, replay=True)
        assert settings.credentials == {}

    def test_valores_por_defecto(self, tmp_path):
        replay = load_config(self.write(tmp_path)).replay
        assert (replay.base_balance, replay.quote_balance) == (D("10"), D("0.5"))
        assert (replay.latency_ms, replay.competitor_pct, replay.seed) == (300, D(0), 0)

    def test_hace_falta_la_seccion(self, tmp_path):
        with pytest.raises(ConfigError, match=r"\[replay\]"):
            load_settings(self.write(tmp_path, ""), tmp_path / "no.env", False, replay=True)

    def test_clave_desconocida(self, tmp_path):
        with pytest.raises(ConfigError, match="replay"):
            load_config(self.write(tmp_path, REPLAY + "otra = 1\n"))

    def test_competidor_es_un_porcentaje(self, tmp_path):
        with pytest.raises(ConfigError, match="competitor_pct"):
            load_config(self.write(tmp_path, REPLAY + 'competitor_pct = "101"\n'))

    def test_latencia_cero_vale(self, tmp_path):
        assert load_config(self.write(tmp_path, REPLAY + "latency_ms = 0\n")).replay.latency_ms == 0

    def test_sin_replay_las_keys_siguen_mandando(self, tmp_path):
        settings = load_settings(self.write(tmp_path), tmp_path / "no.env", False)
        assert settings.mode == "read-only" and not settings.replay
