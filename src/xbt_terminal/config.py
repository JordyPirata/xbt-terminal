"""Loading and validation of config.toml and .env; mode resolution.

Validation is strict on purpose: a mistyped `[risk]` key silently ignored would
leave a limit unapplied. Amounts are strings in the TOML ("0.05"), because a TOML
number reads as float.

Spec: RF-12, RF-13, RNF-08 · design.md §8 · Task: T-03
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, get_args

from dotenv import dotenv_values

from .core.bot import MIN_COOLDOWN_S, MIN_FILL_TIMEOUT_MS, BotLimits
from .core.inventory import InventoryTarget
from .core.models import Timeframe, VenueId
from .core.risk import RiskLimits
from .core.secrets import Redactor, Secret
from .paths import env_permission_problem

Mode = Literal["read-only", "dry-run", "live"]
LogLevel = Literal["debug", "info", "warning", "error"]

VENUE_IDS: tuple[VenueId, ...] = get_args(VenueId)
ENV_PREFIX: dict[VenueId, str] = {"nk": "NONKYC", "nx": "NEOXA"}

# RNF-10: no stablecoins; the pair is always BTCB2 against BTC
REQUIRED_BASE, REQUIRED_QUOTE = "BTCB2", "BTC"

# RNF-03: published by Neoxa minus the 20% margin
NEOXA_MAX = {"general_per_min": 480, "orders_per_10s": 8, "cancels_per_10s": 16}
BALANCES_MAX_EVERY_S = 10  # RF-05


class ConfigError(ValueError):
    """Invalid configuration; the message names the key (`risk.max_order_pct`)."""


@dataclass(frozen=True)
class VenueConfig:
    id: VenueId
    name: str
    symbol: str  # uppercase, with the venue's separator: BTCB2/BTC, BTCB2_BTC
    maker_fee: Decimal
    taker_fee: Decimal
    # % discount on the fee (paying with the venue token): used by the cross panel;
    # by the bot, only with `fee_token` and its balance (RF-24, D-38, D-63)
    fee_discount_pct: Decimal = Decimal(0)
    fee_token: str | None = None  # token ticker, uppercase
    fee_token_min: Decimal | None = None  # minimum available for the bot to count on it

    @property
    def base(self) -> str:
        return _split_symbol(self.symbol)[0]

    @property
    def quote(self) -> str:
        return _split_symbol(self.symbol)[1]


@dataclass(frozen=True)
class UiConfig:
    book_levels: int
    default_timeframe: Timeframe


@dataclass(frozen=True)
class PollingConfig:
    neoxa_private_every_s: int
    balances_every_s: int
    rest_fallback_every_s: int
    value_every_s: int = 300  # account value in the journal (RF-32)


@dataclass(frozen=True)
class RateLimitConfig:
    general_per_min: int
    orders_per_10s: int | None = None
    cancels_per_10s: int | None = None


@dataclass(frozen=True)
class LogConfig:
    level: LogLevel = "info"  # RNF-19


@dataclass(frozen=True)
class BotConfig:
    enabled: bool  # in live it only arms with true (RF-18)
    limits: BotLimits


@dataclass(frozen=True)
class ReplayConfig:
    """`[replay]`: the simulated venues of a replay (RF-34, D-80)."""

    base_balance: Decimal  # BTCB2 on each venue
    quote_balance: Decimal  # BTC on each venue
    latency_ms: int = 300  # from sending to reaching the venue: Neoxa's ack p50
    competitor_pct: Decimal = Decimal(0)  # probability of losing each level
    seed: int = 0


@dataclass(frozen=True)
class AppConfig:
    venues: dict[VenueId, VenueConfig]
    ui: UiConfig
    polling: PollingConfig
    risk: RiskLimits
    ratelimit: dict[VenueId, RateLimitConfig]
    log: LogConfig = LogConfig()
    bot: BotConfig | None = None  # without [bot], no bot (RF-18)
    inventory: InventoryTarget | None = None  # without [inventory], no ratio (RF-26)
    replay: ReplayConfig | None = None  # only read by --replay (RF-34)


@dataclass(frozen=True)
class Credentials:
    api_key: Secret
    api_secret: Secret


@dataclass(frozen=True)
class Settings:
    config: AppConfig
    credentials: dict[VenueId, Credentials]
    mode: Mode
    redactor: Redactor
    warnings: tuple[str, ...] = ()  # shown in red at startup
    replay: bool = False  # simulated venues: trades without keys (RF-34)

    def can_trade(self, venue: VenueId) -> bool:
        if self.replay:
            return True
        return self.mode != "read-only" and venue in self.credentials


# --- reading values ------------------------------------------------------------


class _Section:
    """View of a TOML table remembering its path and which keys were read."""

    def __init__(self, data: Any, path: str) -> None:
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: se esperaba una tabla")
        self.data, self.path, self._read = data, path, set()

    def _key(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def _get(self, key: str) -> Any:
        self._read.add(key)
        if key not in self.data:
            raise ConfigError(f"{self._key(key)}: falta")
        return self.data[key]

    def section(self, key: str) -> _Section:
        return _Section(self._get(key), self._key(key))

    def str(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{self._key(key)}: se esperaba un texto no vacío")
        return value

    def int(self, key: str, *, min: int = 1, max: int | None = None) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{self._key(key)}: se esperaba un entero")
        if value < min or (max is not None and value > max):
            bound = f"entre {min} y {max}" if max is not None else f">= {min}"
            raise ConfigError(f"{self._key(key)}: {value} fuera de rango ({bound})")
        return value

    def decimal(self, key: str, *, positive: bool = True) -> Decimal:
        value = self._get(key)
        if not isinstance(value, str):
            raise ConfigError(f'{self._key(key)}: los importes van como string, p. ej. "0.05"')
        try:
            dec = Decimal(value)
        except InvalidOperation:
            raise ConfigError(f"{self._key(key)}: {value!r} no es un número") from None
        if not dec.is_finite() or dec < 0 or (positive and dec == 0):
            raise ConfigError(f"{self._key(key)}: debe ser {'> 0' if positive else '>= 0'}")
        return dec

    def bool(self, key: str) -> bool:
        value = self._get(key)
        if not isinstance(value, bool):
            raise ConfigError(f"{self._key(key)}: se esperaba true o false")
        return value

    def choice(self, key: str, options: tuple[str, ...]) -> Any:
        value = self.str(key)
        if value not in options:
            raise ConfigError(f"{self._key(key)}: {value!r} no es uno de {', '.join(options)}")
        return value

    def done(self) -> None:
        unknown = sorted(set(self.data) - self._read)
        if unknown:
            keys = ", ".join(self._key(k) for k in unknown)
            raise ConfigError(f"clave desconocida: {keys}")


def _split_symbol(symbol: str) -> tuple[str, str]:
    for sep in ("/", "_"):
        base, found, quote = symbol.partition(sep)
        if found:
            return base, quote
    return symbol, ""


def _btc_symbol(sec: _Section) -> str:
    """RNF-10: only BTCB2 against BTC. Validated here so a TOML mistake never gets to
    send an order against a stablecoin."""
    symbol = sec.str("symbol").upper()
    if _split_symbol(symbol) != (REQUIRED_BASE, REQUIRED_QUOTE):
        raise ConfigError(
            f"{sec._key('symbol')}: {symbol!r} no permitido; solo {REQUIRED_BASE} contra "
            f"{REQUIRED_QUOTE} (BTCB2/BTC o BTCB2_BTC), nunca stablecoins"
        )
    return symbol


def _venue_ids(sec: _Section) -> None:
    if set(sec.data) != set(VENUE_IDS):
        found = ", ".join(sorted(sec.data)) or "ninguno"
        raise ConfigError(f"{sec.path}: se esperan {', '.join(VENUE_IDS)}; hay {found}")


# --- loading -------------------------------------------------------------------


def load_config(path: Path) -> AppConfig:
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: no existe (copiar config.example.toml)") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: TOML inválido: {error}") from None

    root = _Section(raw, "")

    venues_sec = root.section("venues")
    _venue_ids(venues_sec)
    venues: dict[VenueId, VenueConfig] = {}
    for venue_id in VENUE_IDS:
        v = venues_sec.section(venue_id)
        venues[venue_id] = VenueConfig(
            id=venue_id,
            name=v.str("name"),
            symbol=_btc_symbol(v),
            maker_fee=v.decimal("maker_fee", positive=False),
            taker_fee=v.decimal("taker_fee", positive=False),
            fee_discount_pct=(
                _percent(v, "fee_discount_pct", positive=False)
                if "fee_discount_pct" in v.data
                else Decimal(0)
            ),
            **_fee_token(v),
        )
        v.done()
    venues_sec.done()

    u = root.section("ui")
    ui = UiConfig(
        book_levels=u.int("book_levels", max=200),
        default_timeframe=u.choice("default_timeframe", get_args(Timeframe)),
    )
    u.done()

    p = root.section("polling")
    polling = PollingConfig(
        neoxa_private_every_s=p.int("neoxa_private_every_s"),
        balances_every_s=p.int("balances_every_s", max=BALANCES_MAX_EVERY_S),
        rest_fallback_every_s=p.int("rest_fallback_every_s"),
        **({"value_every_s": p.int("value_every_s", min=60)} if "value_every_s" in p.data else {}),
    )
    p.done()

    r = root.section("risk")
    risk = RiskLimits(
        max_order_pct=_percent(r, "max_order_pct"),
        max_mid_deviation_pct=r.decimal("max_mid_deviation_pct"),
        max_venue_exposure=r.decimal("max_venue_exposure"),
        stale_after_s=r.int("stale_after_s"),
        hanging_leg_after_s=r.int("hanging_leg_after_s"),
        max_clock_skew_s=r.int("max_clock_skew_s"),
        **({"book_check_s": r.int("book_check_s", min=1)} if "book_check_s" in r.data else {}),
    )
    r.done()

    rl_sec = root.section("ratelimit")
    _venue_ids(rl_sec)
    nk = rl_sec.section("nk")
    nx = rl_sec.section("nx")
    ratelimit: dict[VenueId, RateLimitConfig] = {
        "nk": RateLimitConfig(general_per_min=nk.int("general_per_min")),
        "nx": RateLimitConfig(
            general_per_min=nx.int("general_per_min", max=NEOXA_MAX["general_per_min"]),
            orders_per_10s=nx.int("orders_per_10s", max=NEOXA_MAX["orders_per_10s"]),
            cancels_per_10s=nx.int("cancels_per_10s", max=NEOXA_MAX["cancels_per_10s"]),
        ),
    }
    nk.done()
    nx.done()
    rl_sec.done()

    log = LogConfig()
    if "log" in root.data:  # optional: without it, info
        lg = root.section("log")
        log = LogConfig(level=lg.choice("level", get_args(LogLevel)))
        lg.done()

    bot = _bot(root.section("bot")) if "bot" in root.data else None
    inv = _inventory(root.section("inventory")) if "inventory" in root.data else None
    replay = _replay(root.section("replay")) if "replay" in root.data else None

    if "dryrun" in root.data:
        raise ConfigError(
            "[dryrun] ya no se usa: el dry-run toma los saldos reales de la cuenta (D-16); "
            "bórrala de config.toml"
        )

    root.done()
    return AppConfig(
        venues=venues,
        ui=ui,
        polling=polling,
        risk=risk,
        ratelimit=ratelimit,
        log=log,
        bot=bot,
        inventory=inv,
        replay=replay,
    )


def _replay(s: _Section) -> ReplayConfig:
    """`[replay]` (RF-34, D-80): balances required; latency, competitor and seed optional."""
    extra: dict[str, Any] = {}
    if "latency_ms" in s.data:
        extra["latency_ms"] = s.int("latency_ms", min=0)
    if "competitor_pct" in s.data:
        extra["competitor_pct"] = _percent(s, "competitor_pct", positive=False)
    if "seed" in s.data:
        extra["seed"] = s.int("seed", min=0)
    config = ReplayConfig(
        base_balance=s.decimal("base_balance"), quote_balance=s.decimal("quote_balance"), **extra
    )
    s.done()
    return config


def _inventory(s: _Section) -> InventoryTarget:
    """`[inventory]` (RF-26, D-46): target strictly between 0 and 100; band > 0 and at
    most 50 points (wider would filter nothing)."""
    target = _percent(s, "target_base_pct")
    if target >= 100:
        raise ConfigError(f"{s._key('target_base_pct')}: debe ser menor que 100")
    band = _percent(s, "band_pct")
    if band > 50:
        raise ConfigError(f"{s._key('band_pct')}: como mucho 50 puntos (no {band})")
    s.done()
    return InventoryTarget(target, band)


def _bot(b: _Section) -> BotConfig:
    """`[bot]` (design.md §6.9): a net of 0 leaves no margin for error, so `min_net_pct`
    must be > 0; slippage may be 0 (at the best price). `max_walk_pct` is optional
    and per venue (D-60)."""
    config = BotConfig(
        enabled=b.bool("enabled"),
        limits=BotLimits(
            min_net_pct=_percent(b, "min_net_pct"),
            max_slippage_pct=_percent(b, "max_slippage_pct", positive=False),
            max_qty=b.decimal("max_qty"),
            fill_timeout_ms=b.int("fill_timeout_ms", min=MIN_FILL_TIMEOUT_MS),
            cooldown_s=b.int("cooldown_s", min=MIN_COOLDOWN_S),
            max_trades_per_hour=b.int("max_trades_per_hour"),
            max_daily_btc=b.decimal("max_daily_btc"),
            max_consecutive_failures=b.int("max_consecutive_failures"),
            max_walk_pct=_walk(b.section("max_walk_pct")) if "max_walk_pct" in b.data else {},
            **_move(b),
            **_lead(b),
        ),
    )
    b.done()
    return config


def _move(b: _Section) -> dict[str, Any]:
    """`max_move_pct` and `move_window_ms` (RF-19, D-71): optional; without the first
    there is no filter."""
    out: dict[str, Any] = {}
    if "max_move_pct" in b.data:
        out["max_move_pct"] = _percent(b, "max_move_pct")
    if "move_window_ms" in b.data:
        out["move_window_ms"] = b.int("move_window_ms", min=100, max=60_000)
    return out


def _lead(b: _Section) -> dict[str, Any]:
    """`lead_venue` (RF-20, D-75): optional; one of the venues."""
    if "lead_venue" not in b.data:
        return {}
    return {"lead_venue": b.choice("lead_venue", VENUE_IDS)}


def _fee_token(v: _Section) -> dict[str, Any]:
    """`fee_token` and `fee_token_min` (RF-24, D-63): both or neither, with a
    `fee_discount_pct` to discount and a token outside the pair."""
    has_token, has_min = "fee_token" in v.data, "fee_token_min" in v.data
    if not (has_token or has_min):
        return {}
    if not has_token:
        raise ConfigError(f"{v._key('fee_token')}: falta (va con fee_token_min)")
    if not has_min:
        raise ConfigError(f"{v._key('fee_token_min')}: falta (va con fee_token)")
    token = v.str("fee_token").strip().upper()
    if not token:
        raise ConfigError(f"{v._key('fee_token')}: vacío")
    if token in _split_symbol(_btc_symbol(v)):
        raise ConfigError(f"{v._key('fee_token')}: {token} es un activo del par")
    discount = v.data.get("fee_discount_pct")
    if discount is None or Decimal(str(discount)) == 0:
        raise ConfigError(f"{v._key('fee_discount_pct')}: hace falta con fee_token")
    return {"fee_token": token, "fee_token_min": v.decimal("fee_token_min")}


def _walk(s: _Section) -> dict[VenueId, Decimal]:
    walk = {
        venue_id: _percent(s, venue_id, positive=False)
        for venue_id in VENUE_IDS
        if venue_id in s.data
    }
    s.done()
    return walk


def _percent(sec: _Section, key: str, *, positive: bool = True) -> Decimal:
    value = sec.decimal(key, positive=positive)
    if value > 100:
        raise ConfigError(f"{sec._key(key)}: es un porcentaje, como mucho 100 (no {value})")
    return value


def load_credentials(env: Mapping[str, str | None]) -> dict[VenueId, Credentials]:
    """Key and secret of each venue. Empty = absent; only one of the two = error."""
    creds: dict[VenueId, Credentials] = {}
    for venue_id in VENUE_IDS:
        key_name, secret_name = (
            f"{ENV_PREFIX[venue_id]}_API_KEY",
            f"{ENV_PREFIX[venue_id]}_API_SECRET",
        )
        key, secret = (env.get(key_name) or "").strip(), (env.get(secret_name) or "").strip()
        if bool(key) != bool(secret):
            missing = secret_name if key else key_name
            raise ConfigError(f".env: falta {missing}")
        if key:
            creds[venue_id] = Credentials(api_key=Secret(key), api_secret=Secret(secret))
    return creds


def resolve_mode(credentials: Mapping[VenueId, Credentials], live: bool) -> Mode:
    if not credentials:
        if live:
            raise ConfigError("--live requiere API keys en .env")
        return "read-only"
    return "live" if live else "dry-run"


def load_settings(
    config_path: Path, env_path: Path, live: bool, *, replay: bool = False
) -> Settings:
    """With `replay`, dry-run on simulated venues: `.env` is not even opened (RF-34)."""
    config = load_config(config_path)
    if replay:
        if config.replay is None:
            raise ConfigError("[replay]: hace falta para reproducir una grabación")
        return Settings(config, {}, "dry-run", Redactor([]), replay=True)
    env = dotenv_values(env_path) if Path(env_path).exists() else {}
    credentials = load_credentials(env)
    mode = resolve_mode(credentials, live)
    warnings: tuple[str, ...] = ()
    if problem := env_permission_problem(Path(env_path)):  # RNF-18
        if mode == "live":
            raise ConfigError(problem)
        warnings = (problem,)
    secrets = [s for c in credentials.values() for s in (c.api_key, c.api_secret)]
    return Settings(
        config=config,
        credentials=credentials,
        mode=mode,
        redactor=Redactor(secrets),
        warnings=warnings,
    )
