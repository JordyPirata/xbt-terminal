import pytest

from xbt_terminal.core.commands import (
    BotCommand,
    CancelAll,
    CancelOrder,
    CancelVenue,
    CommandError,
    DoubleOrder,
    ListOrders,
    Percent,
    PlaceOrder,
    SetTimeframe,
    ShowDeposits,
    ShowHelp,
    ShowRebalance,
    ShowResults,
    parse,
)
from xbt_terminal.core.models import D


@pytest.mark.spec("RF-06")
class TestOrdenSimple:
    def test_buy(self):
        assert parse("buy nk 0.01 850") == PlaceOrder("nk", "buy", D("0.01"), D("850"))

    def test_sell(self):
        assert parse("sell nx 0.5 900.25") == PlaceOrder("nx", "sell", D("0.5"), D("900.25"))

    def test_conserva_los_decimales_escritos(self):
        cmd = parse("buy nk 0.0100 850.00")
        assert str(cmd.qty) == "0.0100" and str(cmd.price) == "850.00"

    def test_mayusculas_y_espacios(self):
        assert parse("  BUY   NK 0.01   850 ") == PlaceOrder("nk", "buy", D("0.01"), D("850"))

    @pytest.mark.parametrize(
        "text,match",
        [
            ("buy zz 0.01 850", "venue"),
            ("buy nk abc 850", "cantidad"),
            ("buy nk 0.01 -850", "precio"),
            ("buy nk 0 850", "cantidad"),
            ("buy nk 0.01 0", "precio"),
            ("buy nk 0,01 850", "punto"),
            ("buy nk 1e-2 850", "cantidad"),
            ("buy nk NaN 850", "cantidad"),
            ("buy nk 0.01", "uso"),
            ("buy nk 0.01 850 900", "uso"),
        ],
    )
    def test_errores(self, text, match):
        with pytest.raises(CommandError, match=match):
            parse(text)


@pytest.mark.spec("RF-17")
class TestCantidadPorcentual:
    def test_porcentaje(self):
        assert parse("buy nx 25% 0.00725") == PlaceOrder(
            "nx", "buy", Percent(D("25")), D("0.00725")
        )

    def test_venta_con_decimales_y_100(self):
        assert parse("sell nk 12.5% 0.0073").qty == Percent(D("12.5"))
        assert parse("SELL NK 100% 0.0073").qty == Percent(D("100"))

    @pytest.mark.parametrize(
        "text,match",
        [
            ("buy nk 0% 0.0073", "porcentaje"),
            ("buy nk 100.01% 0.0073", "100"),
            ("buy nk 25,5% 0.0073", "punto"),
            ("buy nk %25 0.0073", "cantidad"),
            ("buy nk % 0.0073", "porcentaje"),
            ("buy nk 25%% 0.0073", "porcentaje"),
            ("buy nk 25 % 0.0073", "uso"),
            ("buy nk 0.01 25%", "precio"),
        ],
    )
    def test_errores(self, text, match):
        with pytest.raises(CommandError, match=match):
            parse(text)

    def test_la_doble_no_admite_porcentaje(self):
        with pytest.raises(CommandError, match="cantidad"):
            parse("dbl 25% nk 850 nx 900")


@pytest.mark.spec("RF-07")
class TestOrdenDoble:
    def test_dbl(self):
        assert parse("dbl 0.01 nk 850 nx 900") == DoubleOrder(
            qty=D("0.01"), buy_venue="nk", buy_price=D("850"), sell_venue="nx", sell_price=D("900")
        )

    def test_en_la_otra_direccion(self):
        cmd = parse("dbl 0.02 nx 880 nk 905")
        assert (cmd.buy_venue, cmd.sell_venue) == ("nx", "nk")

    def test_mismo_venue_en_las_dos_piernas(self):
        with pytest.raises(CommandError, match="venues distintos"):
            parse("dbl 0.01 nk 850 nk 900")

    @pytest.mark.parametrize(
        "text",
        [
            "dbl 0.01 nk 850 nx",
            "dbl nk 850 nx 900",
            "dbl 0.01 nk 850 nx 900 1",
            "dbl -1 nk 850 nx 900",
        ],
    )
    def test_errores(self, text):
        with pytest.raises(CommandError):
            parse(text)


@pytest.mark.spec("RF-08")
class TestOrdenesYCancelacion:
    def test_orders(self):
        assert parse("orders") == ListOrders(None)
        assert parse("orders nx") == ListOrders("nx")

    def test_cancel_por_id_respeta_mayusculas(self):
        assert parse("cancel AbC-123") == CancelOrder("AbC-123")

    def test_cancel_venue(self):
        assert parse("cancel nk") == CancelVenue("nk")
        assert parse("CANCEL NX") == CancelVenue("nx")

    def test_cancel_all(self):
        assert parse("cancel all") == CancelAll()

    @pytest.mark.parametrize("text", ["cancel", "cancel a b", "orders zz", "orders nk nx"])
    def test_errores(self, text):
        with pytest.raises(CommandError):
            parse(text)


@pytest.mark.spec("RF-03")
def test_timeframe():
    assert parse("tf 5m") == SetTimeframe("5m")
    assert parse("tf 4h") == SetTimeframe("4h")
    assert parse("TF 1D") == SetTimeframe("1d")
    with pytest.raises(CommandError, match="1m, 5m, 1h, 4h, 1d"):
        parse("tf 2h")


@pytest.mark.parametrize(
    "text,match", [("", "vacío"), ("   ", "vacío"), ("market nk 1", "desconocido")]
)
def test_comando_vacio_o_desconocido(text, match):
    with pytest.raises(CommandError, match=match):
        parse(text)


@pytest.mark.spec("RF-15")
@pytest.mark.parametrize("text", ["help", "HELP", "?"])
def test_ayuda(text):
    assert parse(text) == ShowHelp()


@pytest.mark.spec("RF-15")
def test_ayuda_sin_argumentos():
    with pytest.raises(CommandError, match="uso: help"):
        parse("help buy")


@pytest.mark.spec("RF-18", "RF-23")
@pytest.mark.parametrize(
    ("text", "action"), [("bot on", "on"), ("BOT Off", "off"), ("bot status", "status")]
)
def test_bot(text, action):
    assert parse(text) == BotCommand(action)


@pytest.mark.spec("RF-18")
@pytest.mark.parametrize("text", ["bot", "bot start", "bot on now"])
def test_bot_mal_escrito(text):
    with pytest.raises(CommandError, match=r"bot on\|off\|status"):
        parse(text)


@pytest.mark.spec("RF-25")
class TestResults:
    def test_sin_rango_es_el_dia(self):
        assert parse("results") == ShowResults("dia")

    @pytest.mark.parametrize("arg", ["dia", "7d", "todo", "TODO"])
    def test_rangos(self, arg):
        assert parse(f"results {arg}") == ShowResults(arg.lower())

    @pytest.mark.parametrize("text", ["results ayer", "results dia 7d"])
    def test_errores(self, text):
        with pytest.raises(CommandError):
            parse(text)


@pytest.mark.spec("RF-28")
class TestDeposit:
    def test_sin_argumentos(self):
        assert parse("deposit") == ShowDeposits()

    def test_con_argumentos_es_error(self):
        with pytest.raises(CommandError):
            parse("deposit nk")


@pytest.mark.spec("RF-29")
class TestRebalance:
    def test_sin_argumentos(self):
        assert parse("rebalance") == ShowRebalance()

    def test_con_argumentos_es_error(self):
        with pytest.raises(CommandError):
            parse("rebalance nk")


@pytest.mark.spec("RF-06", "RF-07", "RF-08")
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "comando vacío"),
        (
            "foo",
            "comando desconocido 'foo': buy, sell, dbl, orders, cancel, tf, bot, results,"
            " deposit, rebalance, help",
        ),
        ("buy", "uso: buy <venue> <cantidad|N%> <precio>"),
        ("buy xx 1 1", "venue desconocido 'xx': usar nk o nx"),
        ("buy nk 1,5 1", "cantidad '1,5': usar punto decimal, no coma"),
        ("sell nk abc 1", "cantidad 'abc' no es un número"),
        ("buy nk 0 1", "cantidad debe ser mayor que 0"),
        ("buy nk % 1", "porcentaje vacío: escribir p. ej. 25%"),
        ("buy nk 101% 1", "porcentaje '101%': como mucho 100%"),
        ("dbl 1 nk 1 nk 2", "la orden doble necesita venues distintos en cada pierna"),
        ("orders a b", "uso: orders [venue]"),
        ("cancel", "uso: cancel <order_id> | cancel <venue> | cancel all"),
        ("tf 2m", "timeframe '2m': usar 1m, 5m, 1h, 4h, 1d"),
        ("bot", "uso: bot on|off|status"),
        ("bot x", "uso: bot on|off|status"),
        ("results x", "uso: results [dia|7d|todo]"),
        ("deposit nk", "uso: deposit"),
        ("rebalance nk", "uso: rebalance"),
        ("help me", "uso: help"),
        ("? x", "uso: help"),
    ],
)
def test_texto_exacto_de_cada_error(text, message):
    """Fija los mensajes de error (D-77: partir `parse` no los cambia)."""
    with pytest.raises(CommandError) as exc:
        parse(text)
    assert str(exc.value) == message


@pytest.mark.spec("RF-06", "RF-07")
def test_valores_elegidos_en_minusculas():
    assert parse("TF 1H") == SetTimeframe("1h")
    assert parse("Bot STATUS") == BotCommand("status")
    assert parse("results 7D") == ShowResults("7d")
    assert parse("SELL NX 25% 0.004") == PlaceOrder("nx", "sell", Percent(D("25")), D("0.004"))
