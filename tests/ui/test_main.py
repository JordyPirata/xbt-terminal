"""Arranque: el daemon (config → venues reales, motor y socket) y el cliente
(solo el socket del daemon), sin abrir la pantalla ni la red (D-66)."""

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
    path.chmod(0o600)  # RNF-18: en live no se acepta otro
    return path


def engine_for(tmp_path, live=False, env_path=None, conf=EXAMPLE):
    settings = load_settings(conf, env_path or env(tmp_path), live=live)
    return settings, build_engine(settings, logs=tmp_path / "logs")


# -- el motor del daemon ---------------------------------------------------------------


@pytest.mark.spec("RNF-09", "RF-13", "RF-30")
async def test_construye_el_motor_desde_la_config(tmp_path):
    settings, engine = engine_for(tmp_path, env_path=tmp_path / "no-existe.env")
    venues = engine.hub._venues
    assert isinstance(venues["nk"], NonKYCVenue) and venues["nk"].symbol == "BTCB2/BTC"
    assert isinstance(venues["nx"], NeoxaVenue) and venues["nx"].symbol == "BTCB2_BTC"
    assert engine.mode == "read-only"
    view = client_view(settings)
    assert (view.book_levels, view.timeframe) == (15, "1m")
    await engine.hub.aclose()


@pytest.mark.spec("RF-13")
async def test_dry_run_solo_opera_en_los_venues_con_keys(tmp_path):
    _, engine = engine_for(tmp_path)
    assert engine.mode == "dry-run" and engine.orders is not None
    assert engine.orders.can_trade("nk") and not engine.orders.can_trade("nx")
    await engine.hub.aclose()


@pytest.mark.spec("RNF-16")
async def test_el_desfase_solo_bloquea_en_live(tmp_path):
    _, engine = engine_for(tmp_path)
    assert not engine.orders._live and engine._max_skew_s == 5
    await engine.hub.aclose()
    _, engine = engine_for(tmp_path, live=True)
    assert engine.orders._live
    await engine.hub.aclose()


@pytest.mark.spec("RNF-24")
async def test_el_hub_contrasta_el_book_y_lo_apunta(tmp_path):
    _, engine = engine_for(tmp_path)
    assert engine.hub._book_check_s == 5 and engine.hub._journal is engine.orders._journal
    await engine.hub.aclose()


@pytest.mark.spec("RNF-14")
async def test_un_registro_de_fallos_para_todos(tmp_path):
    """Hub, cuenta y órdenes reportan al mismo, con el journal: la barra los cuenta todos."""
    _, engine = engine_for(tmp_path)
    faults = engine.hub.faults
    assert engine.account.faults is faults and engine.orders.faults is faults
    assert engine.faults == [faults] and faults._journal is engine.orders._journal
    await engine.hub.aclose()


@pytest.mark.spec("RF-13", "RF-07")
async def test_dry_run_cuenta_simulada(tmp_path):
    """Dry-run: saldos reales y órdenes simuladas (D-16); la cuenta lee los mismos
    DryRunVenue que las órdenes, para seguir las piernas (D-15)."""
    _, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True))
    sim = engine.orders._venues
    assert all(isinstance(v, DryRunVenue) for v in sim.values()) and set(sim) == {"nk", "nx"}
    assert all(v._fixed is None and v.inner is engine.hub._venues[k] for k, v in sim.items())
    assert engine.account is not None and engine.account._venues == sim
    assert engine.orders._account is engine.account  # valida con la cuenta (D-18)
    engine.hub.state["nk"].spec = "spec-del-hub"
    assert sim["nk"]._spec_source() == "spec-del-hub"
    await engine.hub.aclose()


@pytest.mark.spec("RF-13", "RF-30")
async def test_solo_lectura_sin_ordenes_ni_cuenta(tmp_path):
    _, engine = engine_for(tmp_path, env_path=tmp_path / "no-existe.env")
    assert engine.orders is None and engine.account is None and engine.bot is None
    assert engine.deposits is None and engine.rebalance is None
    await engine.hub.aclose()


@pytest.mark.spec("RF-06", "RF-05", "RF-09")
async def test_live_opera_con_los_venues_reales(tmp_path):
    _, engine = engine_for(tmp_path, live=True)
    assert engine.mode == "live" and engine.orders is not None
    assert engine.orders.can_trade("nk") and not engine.orders.can_trade("nx")
    assert engine.orders._venues["nk"] is engine.hub._venues["nk"]  # el real, no simulado
    assert engine.account is not None and list(engine.account.state) == ["nk"]
    assert engine.orders._journal is engine.account._journal
    assert engine.orders._journal.mode == "live"
    await engine.hub.aclose()


@pytest.mark.spec("RF-12")
async def test_los_adaptadores_reciben_solo_sus_keys(tmp_path):
    settings, engine = engine_for(tmp_path)
    nk, nx = engine.hub._venues["nk"], engine.hub._venues["nx"]
    assert nk._credentials is settings.credentials["nk"] and nx._credentials is None
    assert nx._private_every_s == settings.config.polling.neoxa_private_every_s
    await engine.hub.aclose()


@pytest.mark.spec("RF-18")
async def test_el_bot_comparte_ordenes_cuenta_y_journal(tmp_path):
    _, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True))
    bot = engine.bot
    assert bot is not None and bot.state.status == "off"
    assert bot._orders is engine.orders and bot._account is engine.account
    assert bot._journal is engine.orders._journal and bot.faults is engine.orders.faults
    assert bot.refusal() is None  # en dry-run se arma aunque enabled = false
    await engine.hub.aclose()


@pytest.mark.spec("RF-18")
async def test_bot_en_live(tmp_path):
    _, engine = engine_for(tmp_path, live=True, env_path=env(tmp_path, nx=True))
    assert engine.bot._live and "enabled" in engine.bot.refusal()  # el ejemplo lo trae apagado
    await engine.hub.aclose()


def conf_with(tmp_path, old, new):
    conf = tmp_path / "config.toml"
    conf.write_text(EXAMPLE.read_text().replace(old, new))
    return conf


@pytest.mark.spec("RF-24")
async def test_el_descuento_llega_al_panel_y_no_al_bot(tmp_path):
    conf = conf_with(
        tmp_path, 'symbol  = "BTCB2_BTC"', 'symbol  = "BTCB2_BTC"\nfee_discount_pct = "50"'
    )
    settings, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True), conf=conf)
    assert client_view(settings).fee_discounts == {"nk": 0, "nx": 50}
    assert not hasattr(engine.bot, "_fee_discounts")  # el bot usa el spec del venue, completo
    await engine.hub.aclose()


@pytest.mark.spec("RF-24")
async def test_el_token_de_fee_llega_al_venue_y_al_bot(tmp_path):
    conf = conf_with(
        tmp_path,
        'symbol  = "BTCB2/BTC"',
        'symbol  = "BTCB2/BTC"\nfee_discount_pct = "25"\nfee_token = "NKYC"\nfee_token_min = "1"',
    )
    _, engine = engine_for(tmp_path, env_path=env(tmp_path, nx=True), conf=conf)
    nk = engine.hub._venues["nk"]
    assert isinstance(nk, NonKYCVenue) and nk._balance_assets == ("BTCB2", "BTC", "NKYC")
    assert engine.bot._fee_tokens == {"nk": FeeToken("NKYC", Decimal("1"), Decimal("25"))}
    assert list(engine.results._pricers) == ["NKYC"]  # resultados valoran su fee (D-65)
    await engine.hub.aclose()


@pytest.mark.spec("RF-25")
async def test_sin_token_de_fee_resultados_no_piden_precios(tmp_path):
    _, engine = engine_for(tmp_path)
    assert engine.results._pricers == {}
    await engine.hub.aclose()


@pytest.mark.spec("RF-25")
@pytest.mark.parametrize("live", [False, True])
async def test_resultados_leen_el_directorio_del_journal(tmp_path, live):
    _, engine = engine_for(tmp_path, live=live)
    assert engine.results is not None and engine.results.directory == tmp_path / "logs"
    await engine.hub.aclose()


@pytest.mark.spec("RF-26", "RF-27")
async def test_el_ratio_de_inventario_llega_al_bot_y_al_panel(tmp_path):
    settings, engine = engine_for(tmp_path)
    target = settings.config.inventory
    assert target is not None
    assert client_view(settings).inventory == target
    assert engine.bot is not None and engine.bot._target == target
    await engine.hub.aclose()


@pytest.mark.spec("RF-28")
@pytest.mark.parametrize("live", [False, True])
async def test_depositos_con_los_venues_con_keys(tmp_path, live):
    """En cualquier modo, solo los venues con keys; los adaptadores reales, no
    los de dry-run (solo se lee)."""
    _, engine = engine_for(tmp_path, live=live)
    assert engine.deposits is not None and engine.deposits.venues == ("nk",)
    assert isinstance(engine.deposits._venues["nk"], NonKYCVenue)
    await engine.hub.aclose()


@pytest.mark.spec("RF-29")
@pytest.mark.parametrize("live", [False, True])
async def test_reequilibrio_con_los_venues_con_keys(tmp_path, live):
    """Como los depósitos: adaptadores reales de los venues con keys (solo lee)."""
    settings, engine = engine_for(tmp_path, live=live)
    assert engine.rebalance is not None and engine.rebalance.venues == ("nk",)
    assert isinstance(engine.rebalance._venues["nk"], NonKYCVenue)
    assert settings.config.bot is not None
    assert client_view(settings).bot_limits == settings.config.bot.limits
    await engine.hub.aclose()


# -- xbt daemon -------------------------------------------------------------------------


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Config y .env en el directorio XDG; se arranca desde otro cwd sin flags."""
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
    """Sustituye el bucle del daemon: guarda el motor y comprueba el lock."""
    seen = {}

    async def fake_run(engine, stop, *, on_ready=None, socket=None, book_depth=None, view=None):
        seen.update(engine=engine, socket=socket, book_depth=book_depth, view=view)
        with pytest.raises(AlreadyRunningError):
            InstanceLock(engine.results.directory).acquire()
        on_ready()

    monkeypatch.setattr("xbt_terminal.__main__.run_daemon", fake_run)
    return seen


@pytest.mark.spec("RF-30", "RNF-17", "RNF-19", "RNF-20")
def test_daemon_corre_el_motor_sin_pantalla(xdg, served, monkeypatch, capsys):
    _, data = xdg
    monkeypatch.setattr(XbtApp, "run", lambda app: pytest.fail("el daemon no abre Textual"))
    main()
    engine = served["engine"]
    assert engine.mode == "dry-run" and engine.orders is not None  # el .env del XDG
    assert engine.journal.directory == data
    assert served["socket"] == data / "xbt.sock"
    assert served["book_depth"] == 15
    assert isinstance(served["view"], ClientView) and served["view"].timeframe == "1m"
    assert "xbt daemon en dry-run" in capsys.readouterr().err
    assert "arranque en dry-run" in (data / "diag.log").read_text()  # RNF-19
    assert not (Path.cwd() / "logs").exists()
    with InstanceLock(data):  # al salir se suelta
        pass


@pytest.mark.spec("RNF-20")
def test_daemon_con_directorio_de_datos(xdg, served, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--data-dir", "datos"])
    main()
    assert served["engine"].journal.directory == tmp_path / "otro" / "datos"


def test_daemon_sin_config_explica_que_hacer(xdg):
    conf, _ = xdg
    (conf / "config.toml").unlink()
    with pytest.raises(SystemExit, match=str(conf / "config.toml")) as exc:
        main()
    assert "config.example.toml" in str(exc.value)


def test_daemon_con_config_invalida_no_arranca(xdg):
    conf, _ = xdg
    text = (conf / "config.toml").read_text()
    (conf / "config.toml").write_text(
        text.replace('symbol  = "BTCB2_BTC"', 'symbol = "BTCB2_USDT"')
    )
    with pytest.raises(SystemExit, match="Configuración inválida"):
        main()


@pytest.mark.spec("RF-30", "RNF-17")
def test_daemon_con_otra_instancia_no_arranca(xdg, served):
    _, data = xdg
    with InstanceLock(data), pytest.raises(SystemExit, match="daemon"):
        main()
    assert "engine" not in served


@pytest.mark.spec("RNF-18")
def test_daemon_live_con_env_abierto_no_arranca(xdg, served, monkeypatch):
    conf, _ = xdg
    (conf / ".env").chmod(0o644)
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--live"])
    with pytest.raises(SystemExit, match="chmod 600"):
        main()
    assert "engine" not in served


@pytest.mark.spec("RF-30", "RNF-18")
def test_daemon_avisa_por_stderr(xdg, served, capsys):
    conf, _ = xdg
    (conf / ".env").chmod(0o644)
    main()
    assert "chmod 600" in capsys.readouterr().err


@pytest.mark.spec("RF-30", "RF-13")
def test_daemon_sin_keys_arranca_en_solo_lectura(xdg, served):
    """Es la única forma que queda de mirar el mercado sin keys (D-66)."""
    conf, _ = xdg
    (conf / ".env").write_text("")
    main()
    engine = served["engine"]
    assert engine.mode == "read-only" and engine.orders is None and engine.journal is None


@pytest.mark.spec("RF-30", "RF-31")
def test_daemon_con_ruta_de_socket_larga_no_arranca(xdg, served, monkeypatch, tmp_path):
    long = tmp_path / ("x" * 120) / "xbt.sock"
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--socket", str(long)])
    with pytest.raises(SystemExit, match=r"demasiado larga.*--socket"):
        main()
    assert "engine" not in served


@pytest.mark.spec("RF-30", "RF-31")
def test_daemon_con_otro_socket(xdg, served, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["xbt", "daemon", "--socket", str(tmp_path / "s")])
    main()
    assert served["socket"] == tmp_path / "s"


@pytest.mark.spec("RF-30", "RNF-20")
def test_daemon_con_los_flags_de_siempre(xdg, served, monkeypatch, tmp_path):
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


# -- xbt (el cliente) -----------------------------------------------------------------

VIEW = ClientView(20, "5m", {"nk": Decimal("0"), "nx": Decimal("50")}, None, None)


@pytest.fixture
def connected(monkeypatch, tmp_path):
    """Un daemon que contesta en live con bot y su config; `run` guarda la app y
    mira que el cliente no tome el lock. Construir venues es un fallo: el cliente
    no habla con los venues (D-66)."""
    from xbt_terminal.services.remote import Hello

    seen = {}

    async def fake_probe(path):
        seen["probed"] = path
        return Hello("live", True, VIEW)

    def fake_run(app):
        seen["app"] = app
        InstanceLock(seen["data"]).acquire()  # el cliente no toma el lock (RNF-17)

    def no_venues(*a, **kw):
        raise AssertionError("el cliente no crea venues")

    monkeypatch.setattr("xbt_terminal.__main__.probe", fake_probe)
    monkeypatch.setattr("xbt_terminal.__main__.build_venues", no_venues)
    monkeypatch.setattr("xbt_terminal.__main__.load_settings", no_venues)
    monkeypatch.setattr(XbtApp, "run", fake_run)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "sin-config"))  # no hay config.toml
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    seen["data"] = tmp_path / "state" / "xbt"
    seen["data"].mkdir(parents=True)
    return seen


@pytest.mark.spec("RF-31", "RNF-17", "CA-16")
@pytest.mark.parametrize("argv", [["xbt"], ["xbt", "attach"]])
def test_el_cliente_abre_la_tui_del_daemon(connected, monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", argv)
    main()
    app, data = connected["app"], connected["data"]
    assert connected["probed"] == data / "xbt.sock"
    assert app.remote is not None and app.remote.path == data / "xbt.sock"
    assert app.mode == "live" and app.bot is app.remote.bot  # el modo del daemon
    assert app.account is app.remote.account and app.orders is app.remote.orders
    assert app.results is app.remote.results and app.deposits is app.remote.deposits
    assert app.rebalance is app.remote.rebalance and app.hub is app.remote.market
    # la config de la pantalla llega del daemon, no de config.toml
    assert (app.book_levels, app.timeframe) == (20, "5m")
    assert app._fee_discounts == {"nk": 0, "nx": 50}
    assert app.system_clipboard is system_copy  # D-52
    assert "el daemon sigue" in capsys.readouterr().err
    assert (data / "client.log").exists() and not (data / "diag.log").exists()


@pytest.mark.spec("RF-31")
def test_el_cliente_sin_daemon_no_arranca(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["xbt", "--socket", str(tmp_path / "no")])
    with pytest.raises(SystemExit, match="no hay daemon escuchando"):
        main()


@pytest.mark.spec("RF-31")
@pytest.mark.parametrize("argv", [["xbt", "--live"], ["xbt", "attach", "--live"]])
def test_el_cliente_no_lleva_live(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="el modo es el del daemon"):
        main()


@pytest.mark.spec("RF-31", "RNF-20")
def test_el_cliente_busca_el_socket_en_su_data_dir(connected, monkeypatch, tmp_path):
    data = tmp_path / "datos"
    data.mkdir()
    connected["data"] = data
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["xbt", "--data-dir", "datos"])
    main()
    assert connected["probed"] == data / "xbt.sock"


@pytest.mark.spec("RF-30", "RF-31")
def test_la_ayuda_explica_los_subcomandos(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["xbt", "--help"])
    with pytest.raises(SystemExit):
        main()
    out = capsys.readouterr().out
    for word in ("daemon", "attach", "--socket", "--data-dir", "--live"):
        assert word in out


@pytest.mark.spec("RF-30", "RF-31")
def test_un_subcomando_desconocido_no_arranca(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["xbt", "servidor"])
    with pytest.raises(SystemExit):
        main()
    assert "invalid choice" in capsys.readouterr().err
