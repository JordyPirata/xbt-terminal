"""AccountService (T-17, design.md §6.5): stream privado de cada venue →
estado del canal, órdenes abiertas, fills al journal y balances refrescados
cada `balances_every_s` y tras cada fill."""

import asyncio
import json

import httpx
import pytest

from xbt_terminal.adapters.base import ReadOnlyError, VenueError
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import Balance, ConnState, D, FeeUpdate, Fill, Order
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.account import AccountService

T0 = 1_792_497_600_000


def order(oid="o-1", status="open", filled="0", cid="t-abc123"):
    return Order("nk", oid, cid, "buy", D("0.0072"), D("0.01"), D(filled), status, T0)


def fill(tid="f-1", oid="o-1"):
    return Fill("nk", tid, oid, "buy", D("0.0072"), D("0.004"), D("0.00000006"), "BTC", T0 + 5)


class FakePrivate:
    """Venue privado falso: el test empuja eventos y cuenta las peticiones de saldo."""

    id = "nk"

    def __init__(self, fail_stream=None):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.stream_calls = 0
        self.balance_calls = 0
        self.fail_balances = False
        self.fail_stream = fail_stream

    def push(self, *events):
        for ev in events:
            self.queue.put_nowait(ev)

    async def balances(self):
        self.balance_calls += 1
        if isinstance(self.fail_balances, Exception):
            raise self.fail_balances
        if self.fail_balances:
            raise VenueError("sin red")
        return {"BTC": Balance("BTC", D("0.001")), "BTCB2": Balance("BTCB2", D(self.balance_calls))}

    async def private_stream(self):
        self.stream_calls += 1
        if self.fail_stream:
            raise self.fail_stream
        while True:
            event = await self.queue.get()
            if isinstance(event, Exception):  # cortar un stream ya abierto
                raise event
            yield event


class Rig:
    def __init__(self, tmp_path, every=60.0, **kw):
        self.venue = FakePrivate(**kw)
        self.logs = tmp_path / "logs"
        self.journal = Journal(self.logs, "live", Redactor([]))
        self.seen: list[tuple[str, str]] = []
        self.account = AccountService(
            {"nk": self.venue},
            journal=self.journal,
            balances_every_s=every,
            retry_s=0.01,
            clock=lambda: T0,
        )
        self.account.subscribe(lambda v, what: self.seen.append((v, what)))

    def lines(self):
        files = sorted(self.logs.glob("journal-*.jsonl"))
        return [json.loads(ln) for f in files for ln in f.read_text().splitlines()]

    async def __aenter__(self):
        self.task = asyncio.create_task(self.account.run())
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.journal.close()


async def until(predicate, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.spec("RF-14")
async def test_estado_del_canal_privado(tmp_path):
    async with Rig(tmp_path) as rig:
        rig.venue.push(ConnState("nk", "private", "ws", "up", 120.0, T0))
        st = rig.account.state["nk"]
        await until(lambda: st.conn is not None)
        assert (st.conn.source, st.conn.status, st.conn_at) == ("ws", "up", T0)
        assert ("nk", "conn") in rig.seen


@pytest.mark.spec("RF-08")
async def test_ordenes_abiertas_y_terminadas(tmp_path):
    async with Rig(tmp_path) as rig:
        st = rig.account.state["nk"]
        rig.venue.push(order("a"), order("b"))
        await until(lambda: len(st.orders) == 2)
        rig.venue.push(order("a", status="cancelled"))
        await until(lambda: list(st.orders) == ["b"])
        assert ("nk", "orders") in rig.seen


@pytest.mark.spec("RF-05", "RF-09", "RF-11")
async def test_un_fill_va_al_journal_y_refresca_balances(tmp_path):
    async with Rig(tmp_path) as rig:
        st = rig.account.state["nk"]
        await until(lambda: st.balances is not None)  # carga inicial
        assert rig.venue.balance_calls == 1
        rig.venue.push(order("o-1"), fill())
        await until(lambda: rig.venue.balance_calls == 2)
        await until(lambda: st.balances["BTCB2"].available == D("2"))
        assert st.fills == [fill()]
        (line,) = rig.lines()
        assert (line["event"], line["venue"], line["client_id"]) == ("fill", "nk", "t-abc123")
        assert line["data"] == {
            "trade_id": "f-1",
            "order_id": "o-1",
            "side": "buy",
            "qty": "0.004",
            "price": "0.0072",
            "fee": "0.00000006",
            "fee_asset": "BTC",
        }
        assert ("nk", "fill") in rig.seen and ("nk", "balances") in rig.seen


@pytest.mark.spec("RF-05")
async def test_balances_cada_pocos_segundos(tmp_path):
    async with Rig(tmp_path, every=0.05) as rig:
        await until(lambda: rig.venue.balance_calls >= 3, timeout=1.0)


@pytest.mark.spec("RF-05")
async def test_un_fallo_de_saldos_conserva_los_anteriores(tmp_path):
    async with Rig(tmp_path, every=0.02) as rig:
        st = rig.account.state["nk"]
        await until(lambda: st.balances is not None)
        before = st.balances
        rig.venue.fail_balances = True
        calls = rig.venue.balance_calls
        await until(lambda: rig.venue.balance_calls > calls + 1)
        assert st.balances == before and "sin red" in (st.error or "")


@pytest.mark.spec("RF-13")
async def test_sin_keys_no_reintenta_en_bucle(tmp_path):
    """ReadOnlyError no es un fallo pasajero: se anota y no se reintenta."""
    async with Rig(tmp_path, fail_stream=ReadOnlyError("NonKYC: sin keys")) as rig:
        await until(lambda: rig.venue.stream_calls == 1)
        await asyncio.sleep(0.05)  # retry_s es 0.01: habría reintentado varias veces
        assert rig.venue.stream_calls == 1


@pytest.mark.spec("RF-09")
async def test_fill_sin_orden_conocida_va_sin_client_id(tmp_path):
    """Neoxa no dice la orden de un fill (D-14)."""
    async with Rig(tmp_path) as rig:
        rig.venue.push(Fill("nk", "f-9", None, "sell", D("0.0073"), D("0.01"), D("0"), None, T0))
        await until(lambda: rig.lines())
        line = rig.lines()[0]
        assert line["client_id"] is None and line["data"]["order_id"] is None


@pytest.mark.spec("RF-07")
async def test_reenvia_cada_evento_a_quien_lo_pida(tmp_path):
    async with Rig(tmp_path) as rig:
        got = []
        rig.account.subscribe_events(lambda v, ev: got.append((v, ev)))
        rig.venue.push(order("a"), fill())
        await until(lambda: len(got) == 2)
        assert got == [("nk", order("a")), ("nk", fill())]


@pytest.mark.spec("RF-05")
async def test_una_orden_propia_tambien_refresca_balances(tmp_path):
    """Abrir o cancelar mueve saldo entre disponible y bloqueado."""
    async with Rig(tmp_path) as rig:
        st = rig.account.state["nk"]
        await until(lambda: st.balances is not None)
        rig.venue.push(order("a"))
        await until(lambda: rig.venue.balance_calls == 2)


def errors(rig):
    return [
        (ln["data"]["where"], ln["data"]["error"]) for ln in rig.lines() if ln["event"] == "error"
    ]


@pytest.mark.spec("RF-11", "RF-08")
async def test_un_error_del_canal_privado_va_al_journal_una_vez(tmp_path):
    """D-22: el "BUY" de NonKYC tiraba el stream cada pocos segundos y no quedaba rastro."""
    async with Rig(
        tmp_path, fail_stream=VenueError("NonKYC: lado de trade desconocido 'BUY'")
    ) as rig:
        await until(lambda: rig.venue.stream_calls >= 5)
        assert errors(rig) == [("private", "NonKYC: lado de trade desconocido 'BUY'")]
        rig.venue.fail_stream = VenueError("otro")
        await until(lambda: len(errors(rig)) == 2)
        assert errors(rig)[-1] == ("private", "otro")


@pytest.mark.spec("RF-11", "RF-05")
async def test_un_error_de_balances_va_al_journal_una_vez_por_racha(tmp_path):
    async with Rig(tmp_path, every=0.01) as rig:
        rig.venue.fail_balances = True
        start = rig.venue.balance_calls
        await until(lambda: rig.venue.balance_calls >= start + 5)
        assert errors(rig) == [("balances", "sin red")]
        rig.venue.fail_balances = False
        ok = rig.venue.balance_calls
        await until(lambda: rig.venue.balance_calls >= ok + 2)
        rig.venue.fail_balances = True  # una racha nueva se vuelve a anotar
        await until(lambda: len(errors(rig)) == 2)


@pytest.mark.spec("RF-11", "RF-08")
async def test_el_mismo_error_tras_recuperarse_se_vuelve_a_anotar(tmp_path):
    async with Rig(tmp_path) as rig:
        rig.venue.push(VenueError("caído"))
        await until(lambda: len(errors(rig)) == 1)
        rig.venue.push(VenueError("caído"))  # misma racha: no se repite
        await until(lambda: rig.venue.stream_calls >= 3)
        rig.venue.push(order("a"))  # llegan órdenes: el canal funciona
        await until(lambda: "a" in rig.account.state["nk"].orders)
        rig.venue.push(VenueError("caído"))
        await until(lambda: len(errors(rig)) == 2)


@pytest.mark.spec("RF-05")
async def test_el_intervalo_no_suma_lo_que_tarda_la_lectura(tmp_path):
    """CA-01 vio saldos de 10.4 s con un refresco de 10 s: se esperaba 10 s después
    de terminar cada lectura (~0.4 s). Las lecturas deben empezar cada 10 s."""
    import time
    from itertools import pairwise

    starts = []

    async def slow_balances():
        starts.append(time.perf_counter())
        await asyncio.sleep(0.06)
        return {"BTC": Balance("BTC", D("1"))}

    async with Rig(tmp_path, every=0.2) as rig:
        rig.venue.balances = slow_balances
        await until(lambda: len(starts) >= 4, timeout=2.0)
    gaps = [b - a for a, b in pairwise(starts)]
    assert all(g < 0.24 for g in gaps[1:]), gaps  # 0.2 de intervalo, no 0.26


@pytest.mark.spec("RF-11", "RF-19")
async def test_un_error_sin_texto_guarda_su_tipo(tmp_path):
    """D-51: ConnectTimeout llega con texto vacío; la cuenta lo dejaba en "" y el
    bot lo tomaba por "sin error"."""
    async with Rig(tmp_path, every=0.01) as rig:
        rig.venue.fail_balances = httpx.ConnectTimeout("")
        await until(lambda: rig.account.state["nk"].error is not None)
        assert rig.account.state["nk"].error == "ConnectTimeout"
        await until(lambda: errors(rig) != [])
        assert errors(rig) == [("balances", "ConnectTimeout")]


@pytest.mark.spec("RF-11", "RF-25")
async def test_una_fee_corregida_va_al_journal_sin_otro_fill(tmp_path):
    async with Rig(tmp_path) as rig:
        st = rig.account.state["nk"]
        await until(lambda: st.balances is not None)
        rig.venue.push(order("o-1"), fill())
        await until(lambda: len(rig.lines()) == 1)
        rig.seen.clear()
        rig.venue.push(FeeUpdate("nk", "f-1", "o-1", D("0.0000893166"), "NKYC", T0 + 9))
        await until(lambda: len(rig.lines()) == 2)
        line = rig.lines()[1]
        assert (line["event"], line["venue"], line["client_id"]) == ("fill_fee", "nk", "t-abc123")
        assert line["data"] == {
            "trade_id": "f-1",
            "order_id": "o-1",
            "fee": "0.0000893166",
            "fee_asset": "NKYC",
        }
        (f,) = st.fills
        assert (f.qty, f.fee, f.fee_asset) == (D("0.004"), D("0.0000893166"), "NKYC")
        assert ("nk", "fill") in rig.seen
