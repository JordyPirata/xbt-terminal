from decimal import Decimal
from pathlib import Path

import pytest

from xbt_terminal.config import ConfigError, load_config, load_credentials, load_settings
from xbt_terminal.core.models import D

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config.example.toml"

NK = {"NONKYC_API_KEY": "nk_key_CANARIO_1", "NONKYC_API_SECRET": "nk_sec_CANARIO_2"}
NX = {"NEOXA_API_KEY": "nx_key_CANARIO_3", "NEOXA_API_SECRET": "nx_sec_CANARIO_4"}


def write(tmp_path: Path, text: str, name="config.toml") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def example_with(tmp_path: Path, old: str, new: str) -> Path:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text, f"el ejemplo ya no contiene {old!r}"
    return write(tmp_path, text.replace(old, new, 1))


def env_file(tmp_path: Path, values: dict[str, str]) -> Path:
    path = write(tmp_path, "".join(f"{k}={v}\n" for k, v in values.items()), ".env")
    path.chmod(0o600)  # RNF-18: live accepts no other
    return path


@pytest.mark.spec("RF-12")
class TestConfig:
    def test_example_is_valid(self):
        cfg = load_config(EXAMPLE)
        assert cfg.venues["nk"].symbol == "BTCB2/BTC"
        assert cfg.venues["nx"].symbol == "BTCB2_BTC"
        assert cfg.venues["nx"].maker_fee == D("0.001")
        assert cfg.venues["nx"].taker_fee == D("0.002")
        assert cfg.risk.max_order_pct == D("25")
        assert cfg.ui.book_levels == 15

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match=r"no existe"):
            load_config(tmp_path / "nada.toml")

    def test_malformed_toml(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, "[risk\n"))

    def test_amount_as_float_rejected(self, tmp_path):
        p = example_with(tmp_path, 'max_order_pct          = "25"', "max_order_pct = 25.0")
        with pytest.raises(ConfigError, match=r"risk\.max_order_pct"):
            load_config(p)

    def test_unknown_key_rejected(self, tmp_path):
        p = example_with(tmp_path, "[risk]", '[risk]\nmax_order_qyt = "1"')
        with pytest.raises(ConfigError, match=r"risk\.max_order_qyt"):
            load_config(p)

    def test_venue_missing(self, tmp_path):
        p = example_with(tmp_path, "[venues.nx]", "[venues.zz]")
        with pytest.raises(ConfigError, match=r"venues"):
            load_config(p)

    def test_positive_risk_limits(self, tmp_path):
        p = example_with(tmp_path, 'max_order_pct          = "25"', 'max_order_pct = "-1"')
        with pytest.raises(ConfigError, match=r"risk\.max_order_pct"):
            load_config(p)

    def test_percentage_at_most_100(self, tmp_path):
        """D-19: the per order cap is a % of the inventory."""
        ok = example_with(tmp_path, 'max_order_pct          = "25"', 'max_order_pct = "100"')
        assert load_config(ok).risk.max_order_pct == D("100")
        bad = example_with(tmp_path, 'max_order_pct          = "25"', 'max_order_pct = "100.01"')
        with pytest.raises(ConfigError, match=r"risk\.max_order_pct.*como mucho 100"):
            load_config(bad)

    @pytest.mark.parametrize("old", ["max_order_qty", "max_order_notional"])
    def test_old_keys_rejected(self, tmp_path, old):
        p = example_with(tmp_path, "[risk]", f'[risk]\n{old} = "1"')
        with pytest.raises(ConfigError, match=rf"risk\.{old}"):
            load_config(p)

    @pytest.mark.parametrize("tf", ["4h", "1d"])
    def test_long_timeframes_by_default(self, tmp_path, tf):
        p = example_with(tmp_path, 'default_timeframe = "1m"', f'default_timeframe = "{tf}"')
        assert load_config(p).ui.default_timeframe == tf

    def test_invalid_timeframe(self, tmp_path):
        p = example_with(tmp_path, 'default_timeframe = "1m"', 'default_timeframe = "2h"')
        with pytest.raises(ConfigError, match=r"ui\.default_timeframe"):
            load_config(p)

    @pytest.mark.spec("RF-05")
    @pytest.mark.spec("RNF-16")
    def test_maximum_clock_skew(self, tmp_path):
        assert load_config(EXAMPLE).risk.max_clock_skew_s == 5
        path = example_with(tmp_path, "max_clock_skew_s       = 5", "")
        with pytest.raises(ConfigError, match=r"risk.max_clock_skew_s: falta"):
            load_config(path)

    @pytest.mark.spec("RNF-24")
    def test_book_check(self, tmp_path):
        assert load_config(EXAMPLE).risk.book_check_s == 5
        sin = example_with(tmp_path, "book_check_s           = 5", "")
        assert load_config(sin).risk.book_check_s == 5  # optional: 5 s by default
        cero = example_with(tmp_path, "book_check_s           = 5", "book_check_s = 0")
        with pytest.raises(ConfigError, match=r"risk\.book_check_s"):
            load_config(cero)

    def test_balances_every_10s_at_most(self, tmp_path):
        p = example_with(tmp_path, "balances_every_s      = 10", "balances_every_s = 11")
        with pytest.raises(ConfigError, match=r"polling\.balances_every_s"):
            load_config(p)

    @pytest.mark.spec("RF-19")
    def test_sharp_move_optional(self, tmp_path):
        limits = load_config(EXAMPLE).bot.limits
        assert (limits.max_move_pct, limits.move_window_ms) == (None, 2000)
        on = "# max_move_pct = \"1.0\""
        cfg = load_config(example_with(tmp_path, on, 'max_move_pct = "1.0"\nmove_window_ms = 3000'))
        assert (cfg.bot.limits.max_move_pct, cfg.bot.limits.move_window_ms) == (D("1.0"), 3000)
        with pytest.raises(ConfigError, match=r"bot\.move_window_ms"):
            load_config(example_with(tmp_path, on, 'max_move_pct = "1"\nmove_window_ms = 50'))

    @pytest.mark.spec("RF-32")
    def test_account_value_optional_minimum_60(self, tmp_path):
        assert load_config(EXAMPLE).polling.value_every_s == 300
        on = "# value_every_s       = 300"
        sixty = load_config(example_with(tmp_path, on, "value_every_s = 60"))
        assert sixty.polling.value_every_s == 60
        with pytest.raises(ConfigError, match=r"polling\.value_every_s"):
            load_config(example_with(tmp_path, on, "value_every_s = 59"))

    @pytest.mark.spec("RNF-03")
    @pytest.mark.parametrize(
        "old,new",
        [
            ("general_per_min   = 480", "general_per_min = 481"),
            ("orders_per_10s    = 8", "orders_per_10s = 9"),
            ("cancels_per_10s   = 16", "cancels_per_10s = 17"),
        ],
    )
    def test_neoxa_stays_under_80_percent(self, tmp_path, old, new):
        with pytest.raises(ConfigError, match=r"ratelimit\.nx"):
            load_config(example_with(tmp_path, old, new))


@pytest.mark.spec("RNF-10")
class TestOnlyPairsAgainstBTC:
    @pytest.mark.parametrize(
        "venue_line,bad",
        [
            ('symbol  = "BTCB2/BTC"', "BTCB2/USDT"),
            ('symbol  = "BTCB2/BTC"', "BTCB2/USDC"),
            ('symbol  = "BTCB2_BTC"', "BTCB2_USDT"),
            ('symbol  = "BTCB2_BTC"', "BTCB2_USDC"),
            ('symbol  = "BTCB2_BTC"', "BTC_BTCB2"),
            ('symbol  = "BTCB2_BTC"', "NEOX_BTC"),
            ('symbol  = "BTCB2/BTC"', "BTCB2BTC"),
        ],
    )
    def test_rejects_other_pairs(self, tmp_path, venue_line, bad):
        p = example_with(tmp_path, venue_line, f'symbol = "{bad}"')
        with pytest.raises(ConfigError, match=r"venues\.n[kx]\.symbol.*BTC"):
            load_config(p)

    def test_base_and_quote(self):
        nk = load_config(EXAMPLE).venues["nk"]
        assert (nk.base, nk.quote) == ("BTCB2", "BTC")

    def test_accepts_lowercase(self, tmp_path):
        p = example_with(tmp_path, 'symbol  = "BTCB2_BTC"', 'symbol = "btcb2_btc"')
        assert load_config(p).venues["nx"].quote == "BTC"


@pytest.mark.spec("RF-12", "RNF-08")
class TestCredentials:
    def test_no_keys(self):
        assert load_credentials({}) == {}

    def test_empty_counts_as_absent(self):
        assert load_credentials({"NONKYC_API_KEY": "", "NONKYC_API_SECRET": ""}) == {}

    def test_key_without_secret_is_error(self):
        with pytest.raises(ConfigError, match=r"NONKYC_API_SECRET"):
            load_credentials({"NONKYC_API_KEY": "abc"})

    def test_load_per_venue(self):
        creds = load_credentials(NK)
        assert set(creds) == {"nk"}
        assert creds["nk"].api_key.reveal() == NK["NONKYC_API_KEY"]


@pytest.mark.spec("RF-13")
class TestMode:
    def test_no_keys_is_read_only(self, tmp_path):
        s = load_settings(EXAMPLE, env_file(tmp_path, {}), live=False)
        assert s.mode == "read-only"
        assert not s.can_trade("nk") and not s.can_trade("nx")

    def test_no_env_file_is_read_only(self, tmp_path):
        assert load_settings(EXAMPLE, tmp_path / ".env", live=False).mode == "read-only"

    def test_with_keys_defaults_to_dry_run(self, tmp_path):
        s = load_settings(EXAMPLE, env_file(tmp_path, NK | NX), live=False)
        assert s.mode == "dry-run"

    def test_explicit_live(self, tmp_path):
        assert load_settings(EXAMPLE, env_file(tmp_path, NK | NX), live=True).mode == "live"

    def test_live_without_keys_is_error(self, tmp_path):
        with pytest.raises(ConfigError, match=r"--live"):
            load_settings(EXAMPLE, env_file(tmp_path, {}), live=True)

    def test_keys_of_one_venue(self, tmp_path):
        s = load_settings(EXAMPLE, env_file(tmp_path, NK), live=False)
        assert s.mode == "dry-run"
        assert s.can_trade("nk") and not s.can_trade("nx")


@pytest.mark.spec("CA-07")
def test_settings_do_not_reveal_keys(tmp_path):
    s = load_settings(EXAMPLE, env_file(tmp_path, NK | NX), live=True)
    rendered = repr(s) + str(s)
    for value in (NK | NX).values():
        assert value not in rendered
    assert all(v in [x.reveal() for x in s.redactor.secrets] for v in (NK | NX).values())


@pytest.mark.spec("RF-24")
class TestFeeDiscount:
    def test_no_discount_by_default(self):
        cfg = load_config(EXAMPLE)
        assert cfg.venues["nk"].fee_discount_pct == 0 and cfg.venues["nx"].fee_discount_pct == 0

    def test_percentage(self, tmp_path):
        p = example_with(
            tmp_path, 'symbol  = "BTCB2_BTC"', 'symbol  = "BTCB2_BTC"\nfee_discount_pct = "50"'
        )
        assert load_config(p).venues["nx"].fee_discount_pct == Decimal("50")

    @pytest.mark.parametrize("value", ['"150"', '"-1"', "50"])
    def test_invalid(self, tmp_path, value):
        new = f'symbol  = "BTCB2_BTC"\nfee_discount_pct = {value}'
        p = example_with(tmp_path, 'symbol  = "BTCB2_BTC"', new)
        with pytest.raises(ConfigError, match=r"venues\.nx\.fee_discount_pct"):
            load_config(p)


NK_SYMBOL = 'symbol  = "BTCB2/BTC"'


def nk_token(tmp_path: Path, extra: str) -> Path:
    return example_with(tmp_path, NK_SYMBOL, f"{NK_SYMBOL}\n{extra}")


@pytest.mark.spec("RF-24")
class TestFeeTokenSetup:
    def test_no_token_by_default(self):
        nk = load_config(EXAMPLE).venues["nk"]
        assert nk.fee_token is None and nk.fee_token_min is None

    def test_token_and_minimum(self, tmp_path):
        p = nk_token(tmp_path, 'fee_discount_pct = "25"\nfee_token = "nkyc"\nfee_token_min = "1"')
        nk = load_config(p).venues["nk"]
        assert (nk.fee_token, nk.fee_token_min) == ("NKYC", Decimal("1"))

    @pytest.mark.parametrize(
        ("extra", "key"),
        [
            ('fee_discount_pct = "25"\nfee_token = "NKYC"', "fee_token_min"),
            ('fee_discount_pct = "25"\nfee_token_min = "1"', "fee_token"),
            ('fee_token = "NKYC"\nfee_token_min = "1"', "fee_discount_pct"),
            ('fee_discount_pct = "25"\nfee_token = "NKYC"\nfee_token_min = "0"', "fee_token_min"),
            ('fee_discount_pct = "25"\nfee_token = "BTC"\nfee_token_min = "1"', "fee_token"),
            ('fee_discount_pct = "25"\nfee_token = ""\nfee_token_min = "1"', "fee_token"),
        ],
    )
    def test_invalid(self, tmp_path, extra, key):
        with pytest.raises(ConfigError, match=rf"venues\.nk\.{key}"):
            load_config(nk_token(tmp_path, extra))
