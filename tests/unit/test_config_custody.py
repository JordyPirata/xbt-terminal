"""Self-custody in config.toml and .env (T-103, T-108, RF-37, RF-38, D-85)."""

import pytest

from xbt_terminal.config import ConfigError, load_config, load_settings

from .test_config import EXAMPLE, NK, NX, env_file, write

CUSTODY = """
[custody]
btc_url = "https://btc.test/api"
xbt_url = "https://xbt.test/api"
"""


def config_with(tmp_path, custody=CUSTODY):
    return write(tmp_path, EXAMPLE.read_text(encoding="utf-8") + custody)


def settings(tmp_path, env, custody=CUSTODY, **kwargs):
    return load_settings(
        config_with(tmp_path, custody), env_file(tmp_path, NK | NX | env), live=False, **kwargs
    )


@pytest.mark.spec("RF-38")
class TestCustodySection:
    def test_optional(self):
        assert load_config(EXAMPLE).custody is None

    def test_defaults(self, tmp_path):
        custody = load_config(config_with(tmp_path)).custody
        assert (custody.btc_url, custody.xbt_url) == (
            "https://btc.test/api", "https://xbt.test/api",
        )  # fmt: skip
        assert (custody.every_s, custody.request_every_ms) == (300, 3000)

    def test_one_chain_only(self, tmp_path):
        custody = load_config(
            config_with(tmp_path, '\n[custody]\nxbt_url = "http://localhost:3000"\n')
        ).custody
        assert (custody.btc_url, custody.xbt_url) == (None, "http://localhost:3000")

    def test_explicit_cadence(self, tmp_path):
        custody = load_config(
            config_with(tmp_path, CUSTODY + "every_s = 60\nrequest_every_ms = 0\n")
        ).custody
        assert (custody.every_s, custody.request_every_ms) == (60, 0)

    @pytest.mark.parametrize(
        ("line", "key"),
        [
            ("every_s = 59", r"custody\.every_s"),
            ("request_every_ms = -1", r"custody\.request_every_ms"),
            ("request_every_ms = 60001", r"custody\.request_every_ms"),
            ('every_s = "600"', r"custody\.every_s"),
            ("xpub = 1", r"custody\.xpub"),
        ],
    )
    def test_invalid(self, tmp_path, line, key):
        with pytest.raises(ConfigError, match=key):
            load_config(config_with(tmp_path, CUSTODY + line + "\n"))

    @pytest.mark.parametrize("url", ["mempool.guide/api", "ftp://x/api", ""])
    def test_url_must_be_http(self, tmp_path, url):
        with pytest.raises(ConfigError, match=r"custody\.btc_url"):
            load_config(config_with(tmp_path, f'\n[custody]\nbtc_url = "{url}"\n'))

    def test_without_any_url(self, tmp_path):
        with pytest.raises(ConfigError, match=r"custody"):
            load_config(config_with(tmp_path, "\n[custody]\nevery_s = 600\n"))


@pytest.mark.spec("RF-37")
class TestNoXpubs:
    def test_gap_limit_is_gone(self, tmp_path):
        with pytest.raises(ConfigError, match=r"custody\.gap_limit"):
            load_config(config_with(tmp_path, CUSTODY + "gap_limit = 20\n"))

    def test_settings_carry_no_wallets_and_ignore_the_old_variables(self, tmp_path):
        env = {"WALLET_BTC_XPUBS": "zpub-whatever"}
        loaded = settings(tmp_path, env)
        assert not hasattr(loaded, "wallets")
        assert "zpub-whatever" not in repr(loaded) + str(loaded)
        assert loaded.redactor.redact("zpub-whatever") == "zpub-whatever"

    def test_custody_section_alone_is_valid(self, tmp_path):
        assert settings(tmp_path, {}).config.custody is not None  # addresses come from `watch`
