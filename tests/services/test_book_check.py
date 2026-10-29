"""Contraste del book local con el REST (T-78, RNF-24, D-76): el WS de NonKYC
siguió conectado el 2026-10-29 sin mandar cambios del book y el bot disparó
contra un bid que ya no estaba (d-9820a4, d-f1fe90, d-6eca2e)."""

import asyncio
import json

import httpx
import pytest

from xbt_terminal.adapters.base import VenueError
from xbt_terminal.core.journal import Journal
from xbt_terminal.core.models import BookDelta, BookSnapshot, D, Level
from xbt_terminal.core.secrets import Redactor
from xbt_terminal.services.market import MarketHub

from ..fakes import T0, FakeFeed, book, spec

NOW = T0 + 60_000


class Rig:
    def __init__(self, tmp_path, *, book_check_s=None):
        self.feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.now = NOW
        self.logs = tmp_path / "logs"
        self.journal = Journal(self.logs, "dry-run", Redactor([]))
        self.hub = MarketHub(
            self.feeds, history=50, retry_s=0.01, clock=lambda: self.now,
            journal=self.journal, book_check_s=book_check_s,
        )  # fmt: skip
        for vid in self.feeds:
            self.hub.state[vid].spec = spec(vid)
            self.hub.state[vid].ready = True
        self.hub.apply("nk", book("nk", "0.00720000", "0.00730000"))

    def events(self, name):
        rows = [
            json.loads(line)
            for f in sorted(self.logs.glob("journal-*.jsonl"))
            for line in f.read_text().splitlines()
        ]
        return [r for r in rows if r["event"] == name]


@pytest.mark.spec("RNF-24", "CA-18")
class TestContraste:
    async def test_coincide_confirma_el_book(self, tmp_path):
        rig = Rig(tmp_path)
        rig.now += 300_000  # cinco minutos sin cambios
        assert await rig.hub.check_book("nk") is True
        st = rig.hub.state["nk"]
        assert st.checked_at == rig.now and not st.suspect
        assert "resync" not in rig.feeds["nk"].calls and rig.events("book_resync") == []

    async def test_solo_cuenta_el_tope(self, tmp_path):
        """El REST puede venir recortado: el resto del book no se compara."""
        rig = Rig(tmp_path)
        rig.feeds["nk"].rest_book = BookSnapshot(
            "nk", (Level(D("0.00720000"), D("1")),), (Level(D("0.00730000"), D("0.5")),), T0
        )
        assert await rig.hub.check_book("nk") is True

    @pytest.mark.parametrize(
        "bid, ask",
        [
            ("0.00718273", "0.00730000"),  # otro precio (el bid de d-9820a4 ya no estaba)
            ("0.00720000", "0.00731000"),
        ],
    )
    async def test_no_coincide_pide_book_nuevo_y_lo_apunta(self, tmp_path, bid, ask):
        rig = Rig(tmp_path)
        rig.feeds["nk"].rest_book = book("nk", bid, ask)
        assert await rig.hub.check_book("nk") is False
        assert rig.hub.state["nk"].suspect
        assert rig.feeds["nk"].calls.count("resync") == 1
        (row,) = rig.events("book_resync")
        assert row["venue"] == "nk"
        assert row["data"]["local"] == {
            "bid": ["0.00720000", "1"], "ask": ["0.00730000", "0.5"]
        }  # fmt: skip
        assert row["data"]["rest"] == {"bid": [bid, "1"], "ask": [ask, "0.5"]}

    async def test_otra_cantidad_tampoco_coincide(self, tmp_path):
        rig = Rig(tmp_path)
        rig.feeds["nk"].rest_book = BookSnapshot(
            "nk", (Level(D("0.00720000"), D("0.0021")),), (Level(D("0.00730000"), D("0.5")),), T0
        )
        assert await rig.hub.check_book("nk") is False

    async def test_un_snapshot_nuevo_lo_repara(self, tmp_path):
        rig = Rig(tmp_path)
        rig.feeds["nk"].rest_book = book("nk", "0.00718273", "0.00730000")
        await rig.hub.check_book("nk")
        rig.hub.apply("nk", BookDelta("nk", (Level(D("0.00719"), D("1")),), (), T0))
        assert rig.hub.state["nk"].suspect  # un delta sobre un book malo no lo arregla
        rig.hub.apply("nk", book("nk", "0.00718273", "0.00730000"))
        assert not rig.hub.state["nk"].suspect

    async def test_un_contraste_que_coincide_tambien_lo_repara(self, tmp_path):
        rig = Rig(tmp_path)
        rig.feeds["nk"].rest_book = book("nk", "0.00718273", "0.00730000")
        await rig.hub.check_book("nk")
        rig.feeds["nk"].rest_book = None
        assert await rig.hub.check_book("nk") is True
        assert not rig.hub.state["nk"].suspect

    async def test_mensaje_durante_el_rest_no_cuenta(self, tmp_path):
        """La carrera daría un falso desacuerdo; el mensaje ya da frescura."""
        rig = Rig(tmp_path)
        feed = rig.feeds["nk"]
        rest = book("nk", "0.00718273", "0.00730000")

        async def racing(depth):
            rig.hub.apply("nk", BookDelta("nk", (Level(D("0.00719"), D("1")),), (), T0))
            return rest

        feed.book_snapshot = racing
        assert await rig.hub.check_book("nk") is None
        st = rig.hub.state["nk"]
        assert st.checked_at is None and not st.suspect and rig.events("book_resync") == []

    @pytest.mark.parametrize("exc", [VenueError("NonKYC: HTTP 502"), httpx.ConnectError("sin red")])
    async def test_rest_fallido_no_confirma(self, tmp_path, exc):
        rig = Rig(tmp_path)
        rig.feeds["nk"].rest_book = exc
        assert await rig.hub.check_book("nk") is None
        st = rig.hub.state["nk"]
        assert st.checked_at is None and not st.suspect

    async def test_sin_book_no_se_contrasta(self, tmp_path):
        rig = Rig(tmp_path)
        assert await rig.hub.check_book("nx") is None  # nx sin book todavía
        assert "book" not in rig.feeds["nx"].calls

    async def test_el_hub_contrasta_cada_book_check_s(self, tmp_path):
        rig = Rig(tmp_path, book_check_s=0.01)
        task = asyncio.create_task(rig.hub.run())
        try:
            async with asyncio.timeout(1.0):
                while rig.hub.state["nk"].checked_at is None:
                    await asyncio.sleep(0.005)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_sin_book_check_s_no_contrasta(self, tmp_path):
        rig = Rig(tmp_path)
        task = asyncio.create_task(rig.hub.run())
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert "book" not in rig.feeds["nk"].calls
