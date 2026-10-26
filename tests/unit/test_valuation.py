"""Valor de la cuenta en el tiempo (T-69, RF-32, CA-17, D-69): la muestra se
arma con el inventario de RF-05, se apunta como evento `value` y se lee del
journal como serie con su resumen."""

import pytest

from btcb2_terminal.core.inventory import inventory
from btcb2_terminal.core.models import Balance, D
from btcb2_terminal.core.results import compute
from btcb2_terminal.core.valuation import (
    ValueSample,
    VenueValue,
    last_stretch,
    sample,
    sample_data,
    segments,
    series,
    summarize,
    thin,
)

from .test_results import T0, plain_double, row, ts

EVERY = 300_000


def bal(btc, btcb2):
    return {"BTC": Balance("BTC", D(btc), D(0)), "BTCB2": Balance("BTCB2", D(btcb2), D(0))}


def vrow(stamp, data, mode="live"):
    """Fila `value` del journal (`row` ya usa `at` para la hora)."""
    return {
        "ts": ts(stamp),
        "mode": mode,
        "venue": None,
        "event": "value",
        "client_id": None,
        "data": data,
    }


def value_row(at, total, nk="0", nx="0", every=EVERY, mode="live"):
    venues = {
        "nk": {"btc": nk, "btcb2": "0", "mid": "0.005", "value": nk},
        "nx": {"btc": nx, "btcb2": "0", "mid": "0.005", "value": nx},
    }
    return vrow(at, {"at": at, "every_ms": every, "value": total, "venues": venues}, mode)


def simple(at, total):
    return ValueSample(at, D(total), EVERY, ())


@pytest.mark.spec("RF-32", "CA-17")
class TestMuestra:
    def test_total_y_desglose_por_venue(self):
        balances = {"nk": bal("0.1", "2"), "nx": bal("0.2", "4")}
        mids = {"nk": D("0.005"), "nx": D("0.006")}
        s = sample(inventory(balances, mids), mids, T0, EVERY)
        assert s is not None
        assert s.ts == T0 and s.every_ms == EVERY
        assert s.value == D("0.1") + D("2") * D("0.005") + D("0.2") + D("4") * D("0.006")
        assert s.venues == (
            VenueValue("nk", D("0.1"), D("2"), D("0.005"), D("0.110")),
            VenueValue("nx", D("0.2"), D("4"), D("0.006"), D("0.224")),
        )

    def test_sin_mid_no_hay_muestra(self):
        balances = {"nk": bal("0.1", "2"), "nx": bal("0.2", "4")}
        mids = {"nk": D("0.005"), "nx": None}
        assert sample(inventory(balances, mids), mids, T0, EVERY) is None

    def test_sin_saldo_no_hay_muestra(self):
        mids = {"nk": D("0.005"), "nx": D("0.006")}
        inv = inventory({"nk": bal("0.1", "2"), "nx": None}, mids)
        assert sample(inv, mids, T0, EVERY) is None

    def test_ida_y_vuelta_por_el_journal(self):
        balances = {"nk": bal("0.1", "2"), "nx": bal("0.2", "4")}
        mids = {"nk": D("0.005"), "nx": D("0.006")}
        s = sample(inventory(balances, mids), mids, T0, EVERY)
        assert s is not None
        plain = lambda v: v if isinstance(v, (int, dict)) else str(v)  # noqa: E731
        data = {k: plain(v) for k, v in sample_data(s).items()}
        data["venues"] = {
            vid: {k: str(v) for k, v in item.items()} for vid, item in data["venues"].items()
        }
        got, bad = series([vrow(T0 + 1234, data)], None)
        assert (got, bad) == ((s,), 0)


@pytest.mark.spec("RF-32", "CA-17")
class TestSerie:
    def test_solo_las_del_rango_y_en_orden(self):
        rows = [
            value_row(T0 - 2 * EVERY, "1.0"),
            value_row(T0, "1.1"),
            *plain_double("d-1", T0),
            value_row(T0 + EVERY, "1.2", mode="dry-run"),  # el modo no importa (D-69)
        ]
        got, bad = series(rows, T0)
        assert [(s.ts, s.value) for s in got] == [(T0, D("1.1")), (T0 + EVERY, D("1.2"))]
        assert bad == 0

    def test_una_fila_rota_se_cuenta(self):
        broken = vrow(T0, {"at": T0, "value": "nan?", "venues": {}})
        got, bad = series([broken, value_row(T0 + EVERY, "1")], None)
        assert len(got) == 1 and bad == 1

    def test_viaja_en_el_report(self):
        rows = [*plain_double("d-1", T0), value_row(T0, "1.5", nk="1", nx="0.5")]
        report = compute(rows, since_ms=T0)
        assert [s.value for s in report.values] == [D("1.5")]
        assert report.values[0].venues[0] == VenueValue("nk", D(1), D(0), D("0.005"), D(1))
        assert [d.id for d in report.doubles] == ["d-1"]

    def test_fila_rota_suma_a_las_malas_del_resumen(self):
        report = compute([row("value", T0, value="x")])
        assert report.summary.bad_rows == 1


@pytest.mark.spec("RF-32", "CA-17")
class TestResumen:
    def test_primero_ultimo_cambio_maximo_minimo(self):
        got = summarize(
            tuple(simple(T0 + i * EVERY, v) for i, v in enumerate(("1.0", "1.3", "0.8", "1.1")))
        )
        assert got is not None
        assert (got.first, got.last) == (D("1.0"), D("1.1"))
        assert got.change == D("0.1")
        assert got.change_pct == D("10")
        assert (got.high, got.low, got.count) == (D("1.3"), D("0.8"), 4)
        assert (got.since, got.until) == (T0, T0 + 3 * EVERY)

    def test_sin_muestras(self):
        assert summarize(()) is None

    def test_primero_a_cero_sin_porcentaje(self):
        got = summarize((simple(T0, "0"), simple(T0 + EVERY, "1")))
        assert got is not None and got.change_pct is None


@pytest.mark.spec("RF-32", "CA-17")
class TestHuecos:
    def test_un_hueco_de_mas_de_dos_intervalos_corta(self):
        s = [simple(T0 + i * EVERY, "1") for i in (0, 1, 2, 6, 7)]
        assert [len(seg) for seg in segments(s)] == [3, 2]

    def test_dos_intervalos_justos_no_cortan(self):
        s = [simple(T0, "1"), simple(T0 + 2 * EVERY, "1")]
        assert [len(seg) for seg in segments(s)] == [2]

    def test_sin_muestras_sin_tramos(self):
        assert segments(()) == []

    def test_reducir_conserva_extremos_y_tamano(self):
        s = tuple(simple(T0 + i * EVERY, str(i)) for i in range(1000))
        out = thin(s, 300)
        assert len(out) <= 300
        assert out[0] == s[0] and out[-1] == s[-1]
        assert thin(s[:10], 300) == s[:10]


def split(at, nk, nx):
    venues = (
        VenueValue("nk", D(nk), D(0), D("0.005"), D(nk)),
        VenueValue("nx", D(nx), D(0), D("0.005"), D(nx)),
    )
    return ValueSample(at, D(nk) + D(nx), EVERY, venues)


@pytest.mark.spec("RF-32", "CA-17")
class TestUltimoTramo:
    def test_desde_el_ultimo_salto_del_total(self):
        s = [
            simple(T0 + i * EVERY, v)
            for i, v in enumerate(("1.0", "1.001", "0.5", "0.501", "1.0", "1.002"))
        ]
        assert last_stretch(s) == tuple(s[4:])

    def test_sin_saltos_es_todo(self):
        s = [simple(T0 + i * EVERY, v) for i, v in enumerate(("1.0", "1.04", "1.0"))]
        assert last_stretch(s) == tuple(s)

    def test_un_salto_por_venue_aunque_el_total_no_cambie(self):
        # llega a nx lo que salió de nk en la misma muestra: el total no se mueve
        s = [
            split(T0, "0.6", "0.4"),
            split(T0 + EVERY, "0.3", "0.7"),
            split(T0 + 2 * EVERY, "0.3", "0.701"),
        ]
        assert last_stretch(s) == tuple(s[1:])

    def test_vacio(self):
        assert last_stretch(()) == ()
