"""`[dryrun.balances]` ya no existe: el dry-run usa los saldos reales (D-16)."""

import pytest

from btcb2_terminal.config import ConfigError, load_config

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
