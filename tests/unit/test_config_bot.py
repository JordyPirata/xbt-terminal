"""Sección [bot] de config.toml (T-34, RF-18, RF-22, design.md §6.9)."""

from decimal import Decimal

import pytest

from xbt_terminal.config import ConfigError, load_config

from .test_config import EXAMPLE, example_with


@pytest.mark.spec("RF-18", "RF-22")
def test_el_ejemplo_trae_el_bot_apagado():
    bot = load_config(EXAMPLE).bot
    assert bot is not None and bot.enabled is False
    lim = bot.limits
    assert lim.min_net_pct == Decimal("0.5") and lim.max_slippage_pct == Decimal("0.2")
    assert lim.max_qty == Decimal("100") and lim.max_daily_btc == Decimal("0.01")
    assert (lim.fill_timeout_ms, lim.cooldown_s) == (3000, 10)
    assert (lim.max_trades_per_hour, lim.max_consecutive_failures) == (20, 3)


@pytest.mark.spec("RF-18")
def test_sin_seccion_no_hay_bot(tmp_path):
    text = EXAMPLE.read_text(encoding="utf-8")
    start = text.index("[bot]")
    end = text.index("\n\n", start) if "\n\n" in text[start:] else len(text)
    p = tmp_path / "config.toml"
    p.write_text(text[:start] + text[end:], encoding="utf-8")
    assert load_config(p).bot is None


@pytest.mark.spec("RF-18")
def test_enabled_tiene_que_ser_booleano(tmp_path):
    p = example_with(tmp_path, "enabled = false", 'enabled = "no"')
    with pytest.raises(ConfigError, match=r"bot\.enabled"):
        load_config(p)


@pytest.mark.spec("RF-18", "RF-22")
@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ('min_net_pct = "0.5"', "min_net_pct = 0.5", "min_net_pct"),  # importe como número
        ('min_net_pct = "0.5"', 'min_net_pct = "0"', "min_net_pct"),  # sin margen, no
        ('max_slippage_pct = "0.2"', 'max_slippage_pct = "150"', "max_slippage_pct"),
        ("cooldown_s = 10", "cooldown_s = 1", "cooldown_s"),  # Neoxa: 10 órdenes / 10 s
        ("fill_timeout_ms = 3000", "fill_timeout_ms = 50", "fill_timeout_ms"),
        ("max_trades_per_hour = 20", "max_trades_per_hour = 0", "max_trades_per_hour"),
        ('max_daily_btc = "0.01"', 'max_daily_btc = "0"', "max_daily_btc"),
        ("max_consecutive_failures = 3", "max_consecutive_failures = 0", "max_consecutive"),
    ],
)
def test_valores_invalidos(tmp_path, old, new, key):
    p = example_with(tmp_path, old, new)
    with pytest.raises(ConfigError, match=rf"bot\.{key}"):
        load_config(p)


@pytest.mark.spec("RF-20")
def test_max_walk_pct_es_opcional():
    assert load_config(EXAMPLE).bot.limits.max_walk_pct == {}


@pytest.mark.spec("RF-20")
def test_max_walk_pct_por_venue(tmp_path):
    p = example_with(tmp_path, "enabled = false", 'enabled = false\nmax_walk_pct = { nk = "0.8" }')
    assert load_config(p).bot.limits.max_walk_pct == {"nk": Decimal("0.8")}


@pytest.mark.spec("RF-20")
@pytest.mark.parametrize(
    ("value", "match"),
    [
        ('{ kraken = "1" }', r"bot\.max_walk_pct\.kraken"),  # venue desconocido
        ('{ nk = "150" }', r"bot\.max_walk_pct\.nk"),
        ("{ nk = 1 }", r"bot\.max_walk_pct\.nk"),  # importe como número
        ('"0.8"', r"bot\.max_walk_pct"),  # sin tabla por venue
    ],
)
def test_max_walk_pct_invalido(tmp_path, value, match):
    p = example_with(tmp_path, "enabled = false", f"enabled = false\nmax_walk_pct = {value}")
    with pytest.raises(ConfigError, match=match):
        load_config(p)


@pytest.mark.spec("RF-20")
def test_lead_venue_es_opcional():
    assert load_config(EXAMPLE).bot.limits.lead_venue is None


@pytest.mark.spec("RF-20")
def test_lead_venue(tmp_path):
    line = 'fill_timeout_ms = 3000\nlead_venue = "nx"'
    p = example_with(tmp_path, "fill_timeout_ms = 3000", line)
    assert load_config(p).bot.limits.lead_venue == "nx"


@pytest.mark.spec("RF-20")
def test_lead_venue_invalido(tmp_path):
    line = 'fill_timeout_ms = 3000\nlead_venue = "xx"'
    p = example_with(tmp_path, "fill_timeout_ms = 3000", line)
    with pytest.raises(ConfigError, match=r"bot\.lead_venue"):
        load_config(p)


@pytest.mark.spec("RF-18")
def test_clave_desconocida(tmp_path):
    p = example_with(tmp_path, "enabled = false", 'enabled = false\nmax_qyt = "1"')
    with pytest.raises(ConfigError, match=r"bot\.max_qyt"):
        load_config(p)


@pytest.mark.spec("RF-26")
def test_el_ejemplo_trae_el_ratio_de_inventario():
    inv = load_config(EXAMPLE).inventory
    assert inv is not None
    assert (inv.target_pct, inv.band_pct) == (Decimal("25"), Decimal("10"))


@pytest.mark.spec("RF-26")
def test_sin_seccion_inventory_no_hay_filtro(tmp_path):
    text = EXAMPLE.read_text(encoding="utf-8")
    start = text.index("[inventory]")
    end = text.index("\n\n", start)
    p = tmp_path / "config.toml"
    p.write_text(text[:start] + text[end:], encoding="utf-8")
    assert load_config(p).inventory is None


@pytest.mark.spec("RF-26")
@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ('target_base_pct = "25"', "target_base_pct = 25", "target_base_pct"),
        ('target_base_pct = "25"', 'target_base_pct = "0"', "target_base_pct"),
        ('target_base_pct = "25"', 'target_base_pct = "100"', "target_base_pct"),
        ('band_pct = "10"', 'band_pct = "0"', "band_pct"),
        ('band_pct = "10"', 'band_pct = "60"', "band_pct"),
        ('band_pct = "10"', 'band_pct = "10"\nratio = "1"', "ratio"),
    ],
)
def test_inventory_invalido(tmp_path, old, new, key):
    p = example_with(tmp_path, old, new)
    with pytest.raises(ConfigError, match=rf"inventory\.{key}"):
        load_config(p)
