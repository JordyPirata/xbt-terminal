"""Lectura de los journals para la ventana de resultados (T-40, RF-25, D-44)."""

import json

import pytest

from btcb2_terminal.adapters.base import VenueError
from btcb2_terminal.core.models import D
from btcb2_terminal.services.results import ResultsService

from ..unit.test_results import DAY, T0, fill, plain_double


def write(directory, day, rows, extra=()):
    directory.mkdir(exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra)
    (directory / f"journal-{day}.jsonl").write_text("\n".join(lines) + "\n")


@pytest.mark.spec("RF-25", "CA-11")
class TestServicio:
    def test_rangos_por_dia_utc(self, tmp_path):
        # T0 = 2026-10-22 12:00 UTC
        write(tmp_path, "20261012", plain_double("d-viejo", T0 - 10 * DAY))
        write(tmp_path, "20261020", plain_double("d-semana", T0 - 2 * DAY))
        write(tmp_path, "20261022", plain_double("d-hoy", T0))
        svc = ResultsService(tmp_path, clock=lambda: T0)
        assert [d.id for d in svc.load("dia").doubles] == ["d-hoy"]
        assert [d.id for d in svc.load("7d").doubles] == ["d-semana", "d-hoy"]
        assert [d.id for d in svc.load("todo").doubles] == ["d-viejo", "d-semana", "d-hoy"]

    def test_doble_de_ayer_con_fills_hoy(self, tmp_path):
        midnight = T0 - 12 * 3600_000
        rows = plain_double("d-1", midnight - 500)
        write(tmp_path, "20261021", rows[:2])  # envíos antes de medianoche
        write(tmp_path, "20261022", rows[2:])  # fills después
        svc = ResultsService(tmp_path, clock=lambda: T0)
        assert svc.load("dia").doubles == ()
        assert svc.load("7d").doubles[0].result == D("0.000002")

    def test_sin_directorio_ni_journals(self, tmp_path):
        rep = ResultsService(tmp_path / "nada", clock=lambda: T0).load("todo")
        assert rep.doubles == ()
        assert rep.summary.closed == 0

    def test_lineas_rotas_se_cuentan(self, tmp_path):
        write(tmp_path, "20261022", plain_double(), extra=["{roto"])
        rep = ResultsService(tmp_path, clock=lambda: T0).load("dia")
        assert rep.summary.bad_rows == 1
        assert rep.summary.closed == 1

    def test_otros_ficheros_se_ignoran(self, tmp_path):
        write(tmp_path, "20261022", plain_double())
        (tmp_path / "diag.log").write_text("no es json\n")
        (tmp_path / "journal-raro.jsonl").write_text("{roto\n")
        rep = ResultsService(tmp_path, clock=lambda: T0).load("todo")
        assert rep.summary.bad_rows == 0


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Pricer:
    """`token_price` falso: cuenta las peticiones; `fail` lanza VenueError."""

    def __init__(self, price="0.00013089"):
        self.price, self.calls, self.fail = D(price), 0, False

    async def __call__(self):
        self.calls += 1
        if self.fail:
            raise VenueError("sin red")
        return self.price


def nkyc_double(tmp_path):
    rows = plain_double("d-1", T0)
    rows[2] = fill("nx", "buy", "0.01", "0.0050", fee="0.0025", asset="NKYC")
    write(tmp_path, "20261022", rows)


@pytest.mark.spec("RF-24", "RF-25")
class TestPrecioDelToken:
    async def test_sin_pedir_el_precio_no_se_valora(self, tmp_path):
        nkyc_double(tmp_path)
        svc = ResultsService(tmp_path, clock=lambda: T0, pricers={"NKYC": Pricer()})
        assert svc.load("dia").summary.other_fees == (("NKYC", D("0.0025")),)

    async def test_con_el_precio_se_resta(self, tmp_path):
        nkyc_double(tmp_path)
        svc = ResultsService(tmp_path, clock=lambda: T0, pricers={"NKYC": Pricer()})
        await svc.refresh_prices()
        rep = svc.load("dia")
        assert rep.summary.prices == (("NKYC", D("0.00013089")),)
        assert rep.doubles[0].token_fees == D("0.0025") * D("0.00013089")

    async def test_el_precio_se_guarda_un_minuto(self, tmp_path):
        clock, pricer = Clock(), Pricer()
        svc = ResultsService(tmp_path, clock=clock, pricers={"NKYC": pricer})
        await svc.refresh_prices()
        clock.now += 59_000
        await svc.refresh_prices()
        assert pricer.calls == 1
        clock.now += 1_000
        await svc.refresh_prices()
        assert pricer.calls == 2

    async def test_si_falla_queda_sin_valorar_y_lo_dice(self, tmp_path):
        nkyc_double(tmp_path)
        pricer = Pricer()
        pricer.fail = True
        svc = ResultsService(tmp_path, clock=lambda: T0, pricers={"NKYC": pricer})
        await svc.refresh_prices()
        assert svc.load("dia").summary.other_fees == (("NKYC", D("0.0025")),)
        assert svc.price_errors == {"NKYC": "sin red"}

    async def test_un_fallo_no_borra_el_precio_anterior(self, tmp_path):
        clock, pricer = Clock(), Pricer()
        svc = ResultsService(tmp_path, clock=clock, pricers={"NKYC": pricer})
        await svc.refresh_prices()
        pricer.fail = True
        clock.now += 60_000
        await svc.refresh_prices()
        assert svc.prices == {"NKYC": D("0.00013089")}
