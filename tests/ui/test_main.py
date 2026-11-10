"""Startup: the daemon (config → real venues, engine and socket) and the client (only
the daemon socket), without opening the screen or the network (D-66)."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

from xbt_terminal.__main__ import build_engine, client_view, main
from xbt_terminal.adapters.dryrun import DryRunVenue
from xbt_terminal.adapters.neoxa import NeoxaVenue
from xbt_terminal.adapters.nonkyc import NonKYCVenue
from xbt_terminal.config import load_settings
from xbt_terminal.core.bot import FeeToken
from xbt_terminal.paths import AlreadyRunningError, InstanceLock
from xbt_terminal.services.wire import ClientView
from xbt_terminal.ui.app import XbtApp
from xbt_terminal.ui.clipboard import system_copy

EXAMPLE = Path(__file__).parents[2] / "config.example.toml"


def env(tmp_path, nk=True, nx=False):
    lines = []
    if nk:
        lines += ["NONKYC_API_KEY=k_CANARIO_nk", "NONKYC_API_SECRET=s_CANARIO_nk"]
    if nx:
        lines += ["NEOXA_API_KEY=k_CANARIO_nx", "NEOXA_API_SECRET=s_CANARIO_nx"]
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)  # RNF-18: live accepts no other
    return path


def engine_for(tmp_path, live=False, env_path=None, conf=EXAMPLE):
    settings = load_settings(conf, env_path or env(tmp_path), live=live)
    return settings, build_engine(settings, logs=tmp_path / "logs")


# -- the daemon's engine ---------------------------------------------------------------


@pytest.mark.spec("RNF-09", "RF-13", "RF-30")
async def test_builds_engine_from_config(tmp_path):
    settings, engine = engine_for(tmp_path, env_path=tmp_path / "no-existe.env")
    venues = engine.hub._venues
    assert isinstance(venues["nk"], NonKYCVenue) and venues["nk"].symbol == "BTCB2/BTC"
    assert isinstance(venues["nx"], NeoxaVenue) and venues["nx"].symbol == "BTCB2_BTC"
    assert engine.mode == "read-only"
    view = client_view(settings)
    assert (view.book_levels, view.timeframe) == (15, "1m")
    await engine.hub.aclose()


@pytest.mark.spec("RF-13")
async def test_dry_run_only_trades_venues_with_keys(tmp_path):
    _, engine = engine_for(tmp_path)
    assert engine.mode == "dry-run" and engine.orders is not None
    assert engine.orders.can_trade("nk") and not engine.orders.can_trade("nx")
    await engine.hub.aclose()


@pytest.mark.spec("RNF-16")
async def test_skew_only_blocks_in_live(tmp_path):
    _, engine = engine_for(tmp_path)
    assert not engine.orders._live and engine._max_skew_s == 5
    await engine.hub.aclose()
    _, engine = engine_for(tmp_path, live=True)
    assert engine.orders._live
    await engine.hub.aclose()


@pytest.mark.spec("RNF-24")
async def test_hub_checks_book_and_notes_it(tmp_path):
    _, engine = engine_for(tmp_path)
    assert engine.hub._book_check_s == 5 and engine.hub._journal is engine.orders._journal
    await engine.hub.aclose()


@pytest.mark.spec("RNF-14")
async def test_one_fault_registry_for_all(tmp_path):
    """Hub, account and orders report to the same one, with the journal: the bar counts all."""
    _, engine = engine_for(tmp_path)
    faults = engine.hub.faults
    assert engine.account.faults is faults and engine.orders.faults is faults
    assert engine.faults == [faults] and faults._journal is engine.orders._journal
    await engine.hub.aclose()


@pytest.mark.spec("RF-13", "RF-07")
async def test_dry_run_simulated_account(tmp_path):
    """Dry-run: real balances and simulated orders (D-16); the account reads the same
    DryRunVenues as orders, to follow the legs (D-15)."""
    _, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True))
    sim = engine.orders._venues
    assert all(isinstance(v, DryRunVenue) for v in sim.values()) and set(sim) == {"nk", "nx"}
    assert all(v._fixed is None and v.inner is engine.hub._venues[k] for k, v in sim.items())
    assert engine.account is not None and engine.account._venues == sim
    assert engine.orders._account is engine.account  # validates with the account (D-18)
    engine.hub.state["nk"].spec = "spec-del-hub"
    assert sim["nk"]._spec_source() == "spec-del-hub"
    await engine.hub.aclose()


@pytest.mark.spec("RF-13", "RF-30")
async def test_read_only_no_orders_or_account(tmp_path):
    _, engine = engine_for(tmp_path, env_path=tmp_path / "no-existe.env")
    assert engine.orders is None and engine.account is None and engine.bot is None
    assert engine.deposits is None and engine.rebalance is None
    await engine.hub.aclose()


@pytest.mark.spec("RF-06", "RF-05", "RF-09")
async def test_live_trades_real_venues(tmp_path):
    _, engine = engine_for(tmp_path, live=True)
    assert engine.mode == "live" and engine.orders is not None
    assert engine.orders.can_trade("nk") and not engine.orders.can_trade("nx")
    assert engine.orders._venues["nk"] is engine.hub._venues["nk"]  # the real one, not simulated
    assert engine.account is not None and list(engine.account.state) == ["nk"]
    assert engine.orders._journal is engine.account._journal
    assert engine.orders._journal.mode == "live"
    await engine.hub.aclose()


@pytest.mark.spec("RF-12")
async def test_adapters_get_only_their_keys(tmp_path):
    settings, engine = engine_for(tmp_path)
    nk, nx = engine.hub._venues["nk"], engine.hub._venues["nx"]
    assert nk._credentials is settings.credentials["nk"] and nx._credentials is None
    assert nx._private_every_s == settings.config.polling.neoxa_private_every_s
    await engine.hub.aclose()


@pytest.mark.spec("RF-18")
async def test_bot_shares_orders_account_and_journal(tmp_path):
    _, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True))
    bot = engine.bot
    assert bot is not None and bot.state.status == "off"
    assert bot._orders is engine.orders and bot._account is engine.account
    assert bot._journal is engine.orders._journal and bot.faults is engine.orders.faults
    assert bot.refusal() is None  # in dry-run it arms even with enabled = false
    await engine.hub.aclose()


@pytest.mark.spec("RF-18")
async def test_bot_in_live(tmp_path):
    _, engine = engine_for(tmp_path, live=True, env_path=env(tmp_path, nx=True))
    assert engine.bot._live and "enabled" in engine.bot.refusal()  # the example has it off
    await engine.hub.aclose()


def conf_with(tmp_path, old, new):
    conf = tmp_path / "config.toml"
    conf.write_text(EXAMPLE.read_text().replace(old, new))
    return conf


@pytest.mark.spec("RF-24")
async def test_discount_reaches_panel_not_bot(tmp_path):
    conf = conf_with(
        tmp_path, 'symbol  = "BTCB2_BTC"', 'symbol  = "BTCB2_BTC"\nfee_discount_pct = "50"'
    )
    settings, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True), conf=conf)
    assert client_view(settings).fee_discounts == {"nk": 0, "nx": 50}
    assert not hasattr(engine.bot, "_fee_discounts")  # the bot uses the venue spec, in full
    await engine.hub.aclose()


@pytest.mark.spec("RF-24")
async def test_fee_token_reaches_venue_and_bot(tmp_path):
    conf = conf_with(
        tmp_path,
        'symbol  = "BTCB2/BTC"',
        'symbol  = "BTCB2/BTC"\nfee_discount_pct = "25"\nfee_token = "NKYC"\nfee_token_min = "1"',
    )
    _, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True), conf=conf)
    nk = engine.hub._venues["nk"]
    assert isinstance(nk, NonKYCVenue) and nk._balance_assets == ("BTCB2", "BTC", "NKYC")
    assert engine.bot._fee_tokens == {"nk": FeeToken("NKYC", Decimal("1"), Decimal("25"))}
    assert list(engine.results._pricers) == ["NKYC"]  # results value its fee (D-65)
    await engine.hub.aclose()


@pytest.mark.spec("RF-25")
async def test_no_fee_token_results_ask_no_prices(tmp_path):
    _, engine = engine_for(tmp_path)
    assert engine.results._pricers == {}
    await engine.hub.aclose()


@pytest.mark.spec("RF-25")
@pytest.mark.parametrize("live", [False, True])
async def test_results_read_journal_directory(tmp_path, live):
    _, engine = engine_for(tmp_path, live=live)
    assert engine.results is not None and engine.results.directory == tmp_path / "logs"
    await engine.hub.aclose()


@pytest.mark.spec("RF-26", "RF-27")
async def test_inventory_ratio_reaches_bot_and_panel(tmp_path):
    settings, engine = engine_for(tmp_path)
    target = settings.config.inventory
    assert target is not None
    assert client_view(settings).inventory == target
    assert engine.bot is not None and engine.bot._target == target
    await engine.hub.aclose()


@pytest.mark.spec("RF-28")
@pytest.mark.parametrize("live", [False, True])
async def test_deposits_with_venues_with_keys(tmp_path, live):
    """In any mode, only venues with keys; the real adapters, not the dry-run ones (only
    read)."""
    _, engine = engine_for(tmp_path, live=live)
    assert engine.deposits is not None and engine.deposits.venues == ("nk",)
    assert isinstance(engine.deposits._venues["nk"], NonKYCVenue)
    await engine.hub.aclose()


@pytest.mark.spec("RF-29")
@pytest.mark.parametrize("live", [False, True])
async def test_rebalance_with_venues_with_keys(tmp_path, live):
    """Like deposits: real adapters of venues with keys (read only)."""
    settings, engine = engine_for(tmp_path, live=live)
    assert engine.rebalance is not None and engine.rebalance.venues == ("nk",)
    assert isinstance(engine.rebalance._venues["nk"], NonKYCVenue)
    assert settings.config.bot is not None
    assert client_view(settings).bot_limits == settings.config.bot.limits
    await engine.hub.aclose()


# -- xbt daemon -------------------------------------------------------------------------


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Config and .env in the XDG directory; starts from another cwd without flags."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    conf = tmp_path / "cfg" / "xbt"
    conf.mkdir(parents=True)
    (conf / "config.toml").write_text(EXAMPLE.read_text())
    env(conf)
    elsewhere = tmp_path / "otro"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon"])
    return conf, tmp_path / "state" / "xbt"


@pytest.fixture
def served(monkeypatch):
    """Replace the daemon loop: keep the engine and check the lock."""
    seen = {}

    async def fake_run(engine, stop, *, on_ready=None, socket=None, book_depth=None, view=None):
        seen.update(engine=engine, socket=socket, book_depth=book_depth, view=view)
        with pytest.raises(AlreadyRunningError):
            InstanceLock(engine.results.directory).acquire()
        on_ready()

    monkeypatch.setattr("xbt_terminal.__main__.run_daemon", fake_run)
    return seen


@pytest.mark.spec("RF-30", "RNF-17", "RNF-19", "RNF-20")
def test_daemon_runs_engine_without_screen(xdg, served, monkeypatch, capsys):
    _, data = xdg
    monkeypatch.setattr(XbtApp, "run", lambda app: pytest.fail("el daemon no abre Textual"))
    main()
    engine = served["engine"]
    assert engine.mode == "dry-run" and engine.orders is not None  # the XDG .env
    assert engine.journal.directory == data
    assert served["socket"] == data / "xbt.sock"
    assert served["book_depth"] == 15
    assert isinstance(served["view"], ClientView) and served["view"].timeframe == "1m"
    assert "xbt daemon en dry-run" in capsys.readouterr().err
    assert "starting in dry-run" in (data / "diag.log").read_text()  # RNF-19
    assert not (Path.cwd() / "logs").exists()
    with InstanceLock(data):  # released on exit
        pass


@pytest.mark.spec("RNF-20")
def test_daemon_with_data_directory(xdg, served, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--data-dir", "datos"])
    main()
    assert served["engine"].journal.directory == tmp_path / "otro" / "datos"


def test_daemon_without_config_explains_what_to_do(xdg):
    conf, _ = xdg
    (conf / "config.toml").unlink()
    with pytest.raises(SystemExit, match=str(conf / "config.toml")) as error:
        main()
    assert "config.example.toml" in str(error.value)


def test_daemon_with_invalid_config_does_not_start(xdg):
    conf, _ = xdg
    text = (conf / "config.toml").read_text()
    (conf / "config.toml").write_text(
        text.replace('symbol  = "BTCB2_BTC"', 'symbol = "BTCB2_USDT"')
    )
    with pytest.raises(SystemExit, match="Configuración inválida"):
        main()


@pytest.mark.spec("RF-30", "RNF-17")
def test_daemon_with_another_instance_does_not_start(xdg, served):
    _, data = xdg
    with InstanceLock(data), pytest.raises(SystemExit, match="daemon"):
        main()
    assert "engine" not in served


@pytest.mark.spec("RNF-18")
def test_live_daemon_with_open_env_does_not_start(xdg, served, monkeypatch):
    conf, _ = xdg
    (conf / ".env").chmod(0o644)
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--live"])
    with pytest.raises(SystemExit, match="chmod 600"):
        main()
    assert "engine" not in served


@pytest.mark.spec("RF-30", "RNF-18")
def test_daemon_warns_on_stderr(xdg, served, capsys):
    conf, _ = xdg
    (conf / ".env").chmod(0o644)
    main()
    assert "chmod 600" in capsys.readouterr().err


@pytest.mark.spec("RF-30", "RF-13")
def test_daemon_without_keys_starts_read_only(xdg, served):
    """It is the only way left to watch the market without keys (D-66)."""
    conf, _ = xdg
    (conf / ".env").write_text("")
    main()
    engine = served["engine"]
    assert engine.mode == "read-only" and engine.orders is None and engine.journal is None


@pytest.mark.spec("RF-30", "RF-31")
def test_daemon_with_long_socket_path_does_not_start(xdg, served, monkeypatch, tmp_path):
    long = tmp_path / ("x" * 120) / "xbt.sock"
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--socket", str(long)])
    with pytest.raises(SystemExit, match=r"demasiado larga.*--socket"):
        main()
    assert "engine" not in served


@pytest.mark.spec("RF-30", "RF-31")
def test_daemon_with_another_socket(xdg, served, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--socket", str(tmp_path / "s")])
    main()
    assert served["socket"] == tmp_path / "s"


@pytest.mark.spec("RF-30", "RNF-20")
def test_daemon_with_the_usual_flags(xdg, served, monkeypatch, tmp_path):
    conf, _ = xdg
    other = tmp_path / "otra.toml"
    other.write_text((conf / "config.toml").read_text())
    monkeypatch.setattr(
        sys, "argv",
        ["xbt", "daemon", "--live", "--config", str(other), "--env", str(conf / ".env"),
         "--data-dir", "datos"],
    )  # fmt: skip
    main()
    engine = served["engine"]
    assert engine.mode == "live" and engine.orders._live
    data = tmp_path / "otro" / "datos"
    assert engine.journal.directory == data and served["socket"] == data / "xbt.sock"


# -- xbt (the client) -----------------------------------------------------------------

VIEW = ClientView(20, "5m", {"nk": Decimal("0"), "nx": Decimal("50")}, None, None)


@pytest.fixture
def connected(monkeypatch, tmp_path):
    """A daemon answering in live with a bot and its config; `run` keeps the app and checks
    the client does not take the lock. Building venues is a failure: the client does not
    talk to venues (D-66)."""
    from xbt_terminal.services.remote import Hello

    seen = {}

    async def fake_probe(path):
        seen["probed"] = path
        return Hello("live", True, VIEW)

    def fake_run(app):
        seen["app"] = app
        InstanceLock(seen["data"]).acquire()  # the client does not take the lock (RNF-17)

    def no_venues(*a, **kw):
        raise AssertionError("el cliente no crea venues")

    monkeypatch.setattr("xbt_terminal.__main__.probe", fake_probe)
    monkeypatch.setattr("xbt_terminal.__main__.build_venues", no_venues)
    monkeypatch.setattr("xbt_terminal.__main__.load_settings", no_venues)
    monkeypatch.setattr(XbtApp, "run", fake_run)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "sin-config"))  # no config.toml
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    seen["data"] = tmp_path / "state" / "xbt"
    seen["data"].mkdir(parents=True)
    return seen


@pytest.mark.spec("RF-31", "RNF-17", "CA-16")
@pytest.mark.parametrize("argv", [["xbt"], ["xbt", "attach"]])
def test_client_opens_the_daemon_tui(connected, monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", argv)
    main()
    app, data = connected["app"], connected["data"]
    assert connected["probed"] == data / "xbt.sock"
    assert app.remote is not None and app.remote.path == data / "xbt.sock"
    assert app.mode == "live" and app.bot is app.remote.bot  # the daemon's mode
    assert app.account is app.remote.account and app.orders is app.remote.orders
    assert app.results is app.remote.results and app.deposits is app.remote.deposits
    assert app.rebalance is app.remote.rebalance and app.hub is app.remote.market
    # the screen config comes from the daemon, not config.toml
    assert (app.book_levels, app.timeframe) == (20, "5m")
    assert app._fee_discounts == {"nk": 0, "nx": 50}
    assert app.system_clipboard is system_copy  # D-52
    assert "el daemon sigue" in capsys.readouterr().err
    assert (data / "client.log").exists() and not (data / "diag.log").exists()


@pytest.mark.spec("RF-31")
def test_client_without_daemon_does_not_start(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["xbt", "--socket", str(tmp_path / "no")])
    with pytest.raises(SystemExit, match="no hay daemon escuchando"):
        main()


@pytest.mark.spec("RF-31")
@pytest.mark.parametrize("argv", [["xbt", "--live"], ["xbt", "attach", "--live"]])
def test_client_takes_no_live(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="el modo es el del daemon"):
        main()


@pytest.mark.spec("RF-31", "RNF-20")
def test_client_finds_socket_in_its_data_dir(connected, monkeypatch, tmp_path):
    data = tmp_path / "datos"
    data.mkdir()
    connected["data"] = data
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["xbt", "--data-dir", "datos"])
    main()
    assert connected["probed"] == data / "xbt.sock"


@pytest.mark.spec("RF-30", "RF-31")
def test_help_explains_subcommands(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["xbt", "--help"])
    with pytest.raises(SystemExit):
        main()
    out = capsys.readouterr().out
    for word in ("daemon", "attach", "--socket", "--data-dir", "--live"):
        assert word in out


@pytest.mark.spec("RF-30", "RF-31")
def test_unknown_subcommand_does_not_start(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["xbt", "servidor"])
    with pytest.raises(SystemExit):
        main()
    assert "invalid choice" in capsys.readouterr().err


FIXTURE_LINE = '{"format":1}\n'


@pytest.mark.spec("RF-34")
class TestReplayFlags:
    def argv(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["xbt", *args])

    def test_replay_with_live_does_not_start(self, xdg, served, tmp_path, monkeypatch):
        self.argv(monkeypatch, "daemon", "--replay", "x", "--live", "--data-dir", "d")
        with pytest.raises(SystemExit, match="--live"):
            main()
        assert "engine" not in served

    def test_replay_with_record_does_not_start(self, xdg, served, monkeypatch):
        self.argv(monkeypatch, "daemon", "--replay", "x", "--record", "--data-dir", "d")
        with pytest.raises(SystemExit, match="--record"):
            main()

    def test_replay_requires_data_directory(self, xdg, served, monkeypatch):
        self.argv(monkeypatch, "daemon", "--replay", "x")
        with pytest.raises(SystemExit, match="--data-dir"):
            main()

    def test_replay_with_bad_file_says_so(self, xdg, served, tmp_path, monkeypatch):
        conf, _ = xdg
        text = (conf / "config.toml").read_text()
        (conf / "config.toml").write_text(
            text + '\n[replay]\nbase_balance = "1"\nquote_balance = "1"\n'
        )
        bad = tmp_path / "mala.jsonl"
        bad.write_text("no\n")
        self.argv(monkeypatch, "daemon", "--replay", str(bad), "--data-dir", "d")
        with pytest.raises(SystemExit, match=r"mala\.jsonl"):
            main()

    def test_replay_without_section_says_so(self, xdg, served, tmp_path, monkeypatch):
        good = tmp_path / "g.jsonl"
        good.write_text(FIXTURE_LINE)
        self.argv(monkeypatch, "daemon", "--replay", str(good), "--data-dir", "d")
        with pytest.raises(SystemExit, match=r"\[replay\]"):
            main()

    def test_report_without_replay_says_so(self, xdg, monkeypatch):
        self.argv(monkeypatch, "report")
        with pytest.raises(SystemExit, match="--replay"):
            main()


@pytest.mark.spec("RF-33")
def test_daemon_with_record_records_in_the_data_directory(xdg, served, monkeypatch):
    _, data = xdg
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--record"])
    main()
    assert len(list(data.glob("books-*.jsonl"))) == 1


@pytest.mark.spec("RF-33")
def test_daemon_without_record_does_not_record(xdg, served):
    _, data = xdg
    main()
    assert not list(data.glob("books-*.jsonl"))
