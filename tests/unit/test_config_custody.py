"""Self-custody in config.toml and .env (T-103, RF-37, RF-38, RNF-25, D-84)."""

import pytest

from xbt_terminal.config import ConfigError, load_config, load_settings
from xbt_terminal.core.wallet import RECEIVE, address

from .test_config import EXAMPLE, NK, NX, env_file, write
from .test_wallet import BIP32_PARENT, BIP84_RECEIVE_0, BIP84_ZPUB, with_version

CUSTODY = """
[custody]
btc_url = "https://btc.test/api"
xbt_url = "https://xbt.test/api"
"""
BOTH = {"WALLET_BTC_XPUBS": BIP84_ZPUB, "WALLET_XBT_XPUBS": f"{BIP84_ZPUB}, {BIP32_PARENT}"}
YPUB = with_version(BIP84_ZPUB, 0x049D7CB2)


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
        assert (custody.every_s, custody.gap_limit, custody.request_every_ms) == (3600, 20, 3000)

    def test_one_chain_only(self, tmp_path):
        custody = load_config(
            config_with(tmp_path, '\n[custody]\nxbt_url = "http://localhost:3000"\n')
        ).custody
        assert (custody.btc_url, custody.xbt_url) == (None, "http://localhost:3000")

    def test_explicit_cadence_and_gap(self, tmp_path):
        custody = load_config(
            config_with(tmp_path, CUSTODY + "every_s = 60\ngap_limit = 100\nrequest_every_ms = 0\n")
        ).custody
        assert (custody.every_s, custody.gap_limit, custody.request_every_ms) == (60, 100, 0)

    @pytest.mark.parametrize(
        ("line", "key"),
        [
            ("every_s = 59", r"custody\.every_s"),
            ("gap_limit = 0", r"custody\.gap_limit"),
            ("gap_limit = 101", r"custody\.gap_limit"),
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
class TestWallets:
    def test_no_wallets_by_default(self, tmp_path):
        loaded = load_settings(EXAMPLE, env_file(tmp_path, NK | NX), live=False)
        assert loaded.wallets == {}

    def test_keys_per_chain(self, tmp_path):
        wallets = settings(tmp_path, BOTH).wallets
        assert {asset: len(keys) for asset, keys in wallets.items()} == {"BTC": 1, "BTCB2": 2}
        assert address(wallets["BTC"][0], RECEIVE, 0) == BIP84_RECEIVE_0

    def test_empty_counts_as_absent(self, tmp_path):
        env = {"WALLET_BTC_XPUBS": "", "WALLET_XBT_XPUBS": BIP84_ZPUB}
        assert set(settings(tmp_path, env).wallets) == {"BTCB2"}

    def test_wallets_without_custody_section(self, tmp_path):
        with pytest.raises(ConfigError, match=r"\[custody\]"):
            load_settings(EXAMPLE, env_file(tmp_path, NK | NX | BOTH), live=False)

    def test_wallets_of_a_chain_without_its_url(self, tmp_path):
        with pytest.raises(ConfigError, match=r"custody\.btc_url"):
            settings(tmp_path, BOTH, '\n[custody]\nxbt_url = "https://xbt.test/api"\n')

    def test_custody_section_without_wallets(self, tmp_path):
        with pytest.raises(ConfigError, match=r"WALLET_"):
            settings(tmp_path, {})

    def test_refused_key_names_the_variable_and_position(self, tmp_path):
        env = {"WALLET_BTC_XPUBS": f"{BIP84_ZPUB},{YPUB}"}
        with pytest.raises(ConfigError, match=r"WALLET_BTC_XPUBS.*2.*ypub") as raised:
            settings(tmp_path, env)
        assert YPUB not in str(raised.value) and BIP84_ZPUB not in str(raised.value)

    def test_repeated_key(self, tmp_path):
        env = {"WALLET_XBT_XPUBS": f"{BIP84_ZPUB},{with_version(BIP84_ZPUB, 0x0488B21E)}"}
        with pytest.raises(ConfigError, match=r"WALLET_XBT_XPUBS.*repetida"):
            settings(tmp_path, env)

    def test_same_key_on_both_chains_is_fine(self, tmp_path):
        env = {"WALLET_BTC_XPUBS": BIP84_ZPUB, "WALLET_XBT_XPUBS": BIP84_ZPUB}
        assert set(settings(tmp_path, env).wallets) == {"BTC", "BTCB2"}

    @pytest.mark.spec("RF-34")
    def test_replay_does_not_read_them(self, tmp_path):
        text = EXAMPLE.read_text(encoding="utf-8").replace(
            '# [replay]', "[replay]", 1
        ).replace('# base_balance = "2"', 'base_balance = "2"', 1).replace(
            '# quote_balance = "0.05"', 'quote_balance = "0.05"', 1
        )
        loaded = load_settings(
            write(tmp_path, text + CUSTODY), tmp_path / "none.env", live=False, replay=True
        )
        assert loaded.wallets == {}


@pytest.mark.spec("RNF-25", "CA-22")
def test_settings_do_not_reveal_the_xpubs(tmp_path):
    loaded = settings(tmp_path, BOTH)
    rendered = repr(loaded) + str(loaded)
    revealed = [secret.reveal() for secret in loaded.redactor.secrets]
    for xpub in (BIP84_ZPUB, BIP32_PARENT):
        assert xpub not in rendered
        assert xpub in revealed
    assert loaded.redactor.redact(f"error near {BIP84_ZPUB}!") == "error near ***!"
