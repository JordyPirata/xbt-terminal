"""Al arrancar, el journal se pone al día con los trades del venue (T-68, D-68):
una fee que el venue corrigió mientras la terminal estaba parada, o un fill de
una orden nuestra que pasó entonces, se apuntan; lo ajeno no."""

import pytest

from btcb2_terminal.core.journal import Journal
from btcb2_terminal.core.models import D, Fill, PastFill
from btcb2_terminal.core.secrets import Redactor
from btcb2_terminal.services.account import AccountService
from btcb2_terminal.services.orders import OrderService

from .test_account import T0, Rig, order, until


def trade(tid="f-1", oid="o-1", fee="0.00000006", asset="BTC", ts=T0 + 5):
    return Fill("nk", tid, oid, "buy", D("0.0072"), D("0.004"), D(fee), asset, ts)


def before(rig, *, fee="0.00000006", asset="BTC"):
    """Lo que dejó la sesión anterior: la orden enviada y su fill, con la fee vieja."""
    rig.journal.write("order_sent", venue="nk", client_id="t-abc123", data={"side": "buy"})
    rig.journal.write(
        "order_ack", venue="nk", client_id="t-abc123", data={"order_id": "o-1", "status": "open"}
    )
    rig.journal.write(
        "fill", venue="nk", client_id="t-abc123",
        data={"trade_id": "f-1", "order_id": "o-1", "side": "buy", "qty": "0.004",
              "price": "0.0072", "fee": fee, "fee_asset": asset},
    )  # fmt: skip


async def settled(rig):
    await until(lambda: rig.account.state["nk"].balances is not None)


@pytest.mark.spec("RF-11", "RF-25")
class TestPonerseAlDia:
    async def test_una_fee_corregida_mientras_estaba_parada(self, tmp_path):
        async with Rig(tmp_path) as rig:
            await settled(rig)
            before(rig)
            n = len(rig.lines())
            rig.venue.push(PastFill(trade(fee="0.0000893166", asset="NKYC")))
            await until(lambda: len(rig.lines()) == n + 1)
            line = rig.lines()[-1]
            assert (line["event"], line["client_id"]) == ("fill_fee", "t-abc123")
            assert line["data"] == {
                "trade_id": "f-1", "order_id": "o-1", "fee": "0.0000893166", "fee_asset": "NKYC",
            }  # fmt: skip
            assert ("nk", "fill") in rig.seen

    async def test_la_misma_fee_no_apunta_nada(self, tmp_path):
        async with Rig(tmp_path) as rig:
            await settled(rig)
            before(rig)
            n = len(rig.lines())
            rig.venue.push(PastFill(trade()), order("o-2"))  # la orden: para saber que pasó
            await until(lambda: "o-2" in rig.account.state["nk"].orders)
            assert len(rig.lines()) == n

    async def test_la_ultima_correccion_es_la_que_cuenta(self, tmp_path):
        async with Rig(tmp_path) as rig:
            await settled(rig)
            before(rig)
            rig.journal.write(
                "fill_fee", venue="nk", client_id="t-abc123",
                data={"trade_id": "f-1", "order_id": "o-1", "fee": "0.0000893166",
                      "fee_asset": "NKYC"},
            )  # fmt: skip
            n = len(rig.lines())
            rig.venue.push(PastFill(trade(fee="0.0000893166", asset="NKYC")), order("o-2"))
            await until(lambda: "o-2" in rig.account.state["nk"].orders)
            assert len(rig.lines()) == n

    async def test_un_fill_nuestro_mientras_estaba_parada(self, tmp_path):
        async with Rig(tmp_path) as rig:
            await settled(rig)
            before(rig)
            n = len(rig.lines())
            rig.venue.push(PastFill(trade("f-2", ts=T0 + 7)))
            await until(lambda: len(rig.lines()) == n + 1)
            line = rig.lines()[-1]
            assert (line["event"], line["client_id"]) == ("fill", "t-abc123")
            assert line["data"]["trade_id"] == "f-2" and line["data"]["recovered"] is True
            assert line["data"]["trade_ts"] == T0 + 7

    async def test_lo_ajeno_no_se_apunta(self, tmp_path):
        async with Rig(tmp_path) as rig:
            await settled(rig)
            before(rig)
            n = len(rig.lines())
            rig.venue.push(PastFill(trade("f-9", oid="de-la-web")), order("o-2"))
            await until(lambda: "o-2" in rig.account.state["nk"].orders)
            assert len(rig.lines()) == n

    async def test_en_dry_run_no(self, tmp_path):
        rig = Rig(tmp_path)
        rig.journal = Journal(rig.logs, "dry-run", Redactor([]))
        rig.account = AccountService(
            {"nk": rig.venue}, journal=rig.journal, retry_s=0.01, clock=lambda: T0
        )
        async with rig:
            await settled(rig)
            before(rig)
            n = len(rig.lines())
            rig.venue.push(PastFill(trade(fee="0.0000893166", asset="NKYC")), order("o-2"))
            await until(lambda: "o-2" in rig.account.state["nk"].orders)
            assert len(rig.lines()) == n

    async def test_un_pasado_no_es_un_fill_para_nadie_mas(self, tmp_path):
        async with Rig(tmp_path) as rig:
            seen = []
            rig.account.subscribe_events(lambda vid, ev: seen.append(ev))
            await settled(rig)
            rig.venue.push(PastFill(trade()))
            await until(lambda: seen)
            assert seen == [PastFill(trade())]
            assert list(rig.account.state["nk"].recent_fills) == []
            orders = OrderService(None, {}, rig.journal, None)  # type: ignore[arg-type]
            orders.on_private_event("nk", seen[0])  # no peta ni toca nada
