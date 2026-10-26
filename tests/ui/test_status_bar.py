"""Barra de estado (T-15): conexión y latencia por venue y canal, y modo."""

import asyncio
from dataclasses import replace

import pytest

from btcb2_terminal.core.models import ConnState
from btcb2_terminal.services.market import MarketHub
from btcb2_terminal.ui.panels import BookPanel, CrossPanel
from btcb2_terminal.ui.status import STALE_AFTER_MS, StatusBar, VenueLink, render_status

from ..fakes import T0, FakeFeed
from ..services.test_account import FakePrivate
from .local import local_app

NOW = T0 + 60_000


def link(source="ws", status="up", latency=168.4, since=NOW - 30_000, seen=NOW, trade=False):
    state = ConnState("nk", "market", source, status, latency, since) if source else None
    return VenueLink(market=state, seen_at=seen, can_trade=trade)


def text(nk=None, nx=None, mode="read-only", now=NOW):
    links = {"nk": nk or VenueLink(None, None, False), "nx": nx or VenueLink(None, None, False)}
    return render_status(links, mode, now)


def part(rendered: str, venue: str) -> str:
    """El trozo de la barra de un venue."""
    return next(p for p in rendered.split("│") if venue in p)


@pytest.mark.spec("RF-14")
class TestCanalDeMercado:
    def test_ws_up_con_latencia(self):
        nk = part(text(nk=link()).plain, "NonKYC")
        assert "ws ●" in nk and "168 ms" in nk

    def test_reconectando_y_desde_cuando(self):
        nk = part(text(nk=link(status="reconnecting", latency=None)).plain, "NonKYC")
        assert "reconectando 30s" in nk and " ms" not in nk

    def test_respaldo_rest(self):
        nk = part(text(nk=link(source="rest", latency=240.0)).plain, "NonKYC")
        assert "respaldo ●" in nk and "240 ms" in nk

    def test_caido(self):
        rendered = text(nk=link(source="rest", status="down", latency=None))
        nk = part(rendered.plain, "NonKYC")
        assert "respaldo ✖ caído 30s" in nk
        styles = [
            str(s.style) for s in rendered.spans if "caído" in rendered.plain[s.start : s.end]
        ]
        assert any("red" in st for st in styles)

    def test_sin_estado_todavia(self):
        assert "conectando…" in part(text().plain, "Neoxa")

    def test_latencia_aun_sin_medir(self):
        nk = part(text(nk=link(latency=None)).plain, "NonKYC")
        assert "ws ● — ms" in nk

    def test_aviso_si_el_estado_no_se_renueva(self):
        old = link(seen=NOW - STALE_AFTER_MS - 1)
        nk = part(text(nk=old).plain, "NonKYC")
        assert "sin noticias 15s" in nk
        assert "sin noticias" not in part(text(nk=link(seen=NOW - 4_000)).plain, "NonKYC")

    def test_reconectando_no_avisa_de_silencio(self):
        """Reconectando no hay latidos (D-11): el aviso solo confundiría."""
        old = link(status="reconnecting", latency=None, seen=NOW - STALE_AFTER_MS - 1)
        assert "sin noticias" not in part(text(nk=old).plain, "NonKYC")


def private(source="ws", status="up", since=NOW - 12_000):
    return ConnState("nk", "private", source, status, 300.0, since)


@pytest.mark.spec("RF-14", "RF-13")
class TestCanalPrivado:
    def test_sin_keys(self):
        assert "privado sin keys" in part(text(nk=link(trade=False)).plain, "NonKYC")

    def test_dry_run_es_simulado(self):
        """En dry-run las órdenes y saldos son simulados (D-13): no hay canal real."""
        assert "privado simulado" in part(text(nk=link(trade=True), mode="dry-run").plain, "NonKYC")

    def test_live_aun_sin_canal(self):
        assert "privado —" in part(text(nk=link(trade=True), mode="live").plain, "NonKYC")

    def test_ws_up(self):
        nk = replace(link(trade=True), private=private())
        assert "privado ● ws" in part(text(nk=nk, mode="live").plain, "NonKYC")

    def test_polling_up(self):
        nk = replace(link(trade=True), private=private(source="rest"))
        assert "privado ● rest" in part(text(nk=nk, mode="live").plain, "NonKYC")

    def test_caido_y_desde_cuando(self):
        nk = replace(link(trade=True), private=private(source="rest", status="down"))
        rendered = text(nk=nk, mode="live")
        assert "privado ✖ caído 12s" in part(rendered.plain, "NonKYC")


@pytest.mark.spec("RF-13")
class TestModo:
    def test_solo_lectura(self):
        assert "SOLO LECTURA" in text().plain

    def test_dry_run(self):
        assert "DRY-RUN" in text(mode="dry-run").plain

    def test_live_banner_rojo(self):
        rendered = text(mode="live")
        assert "LIVE · ÓRDENES REALES" in rendered.plain
        span = next(s for s in rendered.spans if "LIVE" in rendered.plain[s.start : s.end])
        assert "on red" in str(span.style)


async def until(pilot, predicate, timeout=1.5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await pilot.pause(0.01)


@pytest.mark.spec("RF-14", "RNF-02")
class TestEnPantalla:
    def make(self, mode="read-only", can_trade=None, account=None):
        self.feeds = {"nk": FakeFeed("nk"), "nx": FakeFeed("nx")}
        self.now = NOW
        hub = MarketHub(self.feeds, history=50, retry_s=0.01, clock=lambda: self.now)
        return local_app(
            hub,
            book_levels=15,
            timeframe="1m",
            mode=mode,
            can_trade=can_trade or {"nk": False, "nx": False},
            account=account,
            clock=lambda: self.now,
        )

    async def test_canal_privado_desde_la_cuenta(self):
        from btcb2_terminal.services.account import AccountService

        venue = FakePrivate()
        account = AccountService({"nk": venue}, retry_s=0.01, clock=lambda: NOW)
        app = self.make(mode="live", can_trade={"nk": True, "nx": False}, account=account)
        async with app.run_test(size=(160, 45)) as pilot:
            bar = app.query_one(StatusBar)
            await until(pilot, lambda: "privado —" in part(bar.text, "NonKYC"))
            venue.push(ConnState("nk", "private", "ws", "up", 300.0, NOW))
            await until(pilot, lambda: "privado ● ws" in part(bar.text, "NonKYC"))
            venue.push(ConnState("nk", "private", "rest", "down", None, NOW - 5_000))
            await until(pilot, lambda: "privado ✖ caído 5s" in part(bar.text, "NonKYC"))

    async def test_arriba_y_el_layout_sigue_cabiendo(self):
        app = self.make()
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            bar = app.query_one(StatusBar)
            assert bar.region.y == 0 and bar.region.height == 1 and bar.region.width == 160
            assert all(p.region.height == 19 for p in app.query(BookPanel))
            cross = app.query_one(CrossPanel).region
            assert cross.y + cross.height <= 45

    async def test_refleja_el_estado_del_hub(self):
        app = self.make()
        async with app.run_test(size=(160, 45)) as pilot:
            bar = app.query_one(StatusBar)
            self.feeds["nx"].push(ConnState("nx", "market", "ws", "up", 165.9, NOW))
            await until(pilot, lambda: "166 ms" in part(bar.text, "Neoxa"))
            self.feeds["nx"].push(ConnState("nx", "market", "ws", "reconnecting", None, NOW))
            await until(pilot, lambda: "reconectando" in part(bar.text, "Neoxa"))

    async def test_modo_y_keys_desde_la_app(self):
        app = self.make(mode="dry-run", can_trade={"nk": True, "nx": False})
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            bar = app.query_one(StatusBar)
            assert "DRY-RUN" in bar.text
            assert "privado simulado" in part(bar.text, "NonKYC")
            assert "privado sin keys" in part(bar.text, "Neoxa")
            assert "modo" not in (app.query_one(CrossPanel).border_subtitle or "")

    async def test_la_antiguedad_avanza_sola(self):
        app = self.make()
        async with app.run_test(size=(160, 45)) as pilot:
            bar = app.query_one(StatusBar)
            self.feeds["nk"].push(ConnState("nk", "market", "ws", "up", 100.0, NOW))
            await until(pilot, lambda: "100 ms" in part(bar.text, "NonKYC"))
            self.now = NOW + STALE_AFTER_MS + 2_000  # sin latidos nuevos
            await until(pilot, lambda: "sin noticias" in part(bar.text, "NonKYC"))


@pytest.mark.spec("RF-14", "RNF-02")
def test_el_peor_caso_cabe_en_160_columnas():
    def bad(venue, source, status):
        state = ConnState(venue, "market", source, status, None, NOW - 600_000)
        priv = ConnState(venue, "private", "rest", "down", None, NOW - 600_000)
        return VenueLink(state, NOW - 600_000, can_trade=True, private=priv)

    for nk_case, nx_case in [
        (("rest", "down"), ("ws", "reconnecting")),
        (("ws", "reconnecting"), ("ws", "reconnecting")),
    ]:
        for mode in ("read-only", "dry-run", "live"):
            links = {"nk": bad("nk", *nk_case), "nx": bad("nx", *nx_case)}
            assert len(render_status(links, mode, NOW).plain) <= 160
            assert len(render_status(links, mode, NOW, uncertain=99).plain) <= 160
            worst = render_status(links, mode, NOW, uncertain=99, faults=99)
            assert len(worst.plain) <= 160


@pytest.mark.spec("RNF-12")
@pytest.mark.parametrize(("n", "label"), [(0, None), (1, "⚠ 1 incierta"), (3, "⚠ 3 inciertas")])
def test_ordenes_inciertas(n, label):
    links = {"nk": VenueLink(None, None, False), "nx": VenueLink(None, None, False)}
    rendered = render_status(links, "live", NOW, uncertain=n).plain
    if label is None:
        assert "incierta" not in rendered
    else:
        assert label in rendered and rendered.index(label) < rendered.index("LIVE")


@pytest.mark.spec("RNF-14")
@pytest.mark.parametrize(("n", "label"), [(0, None), (1, "✖ 1 fallo "), (3, "✖ 3 fallos")])
def test_fallos_internos(n, label):
    links = {"nk": VenueLink(None, None, False), "nx": VenueLink(None, None, False)}
    rendered = render_status(links, "live", NOW, faults=n).plain
    if label is None:
        assert "fallo" not in rendered
    else:
        assert label in rendered and rendered.index(label) < rendered.index("LIVE")


@pytest.mark.spec("RF-14")
async def test_un_cambio_de_estado_se_pinta_al_llegar(monkeypatch):
    """Sin esperar al refresco periódico, que aquí se pone en 60 s."""
    from btcb2_terminal.ui import app as app_module

    monkeypatch.setattr(app_module, "STATUS_EVERY_S", 60.0)
    t = TestEnPantalla()
    app = t.make()
    async with app.run_test(size=(160, 45)) as pilot:
        bar = app.query_one(StatusBar)
        await pilot.pause()
        t.feeds["nk"].push(ConnState("nk", "market", "ws", "up", 99.0, NOW))
        await until(pilot, lambda: "99 ms" in part(bar.text, "NonKYC"), timeout=0.5)


@pytest.mark.spec("RF-23")
class TestBot:
    def links(self):
        return {"nk": VenueLink(None, None, False), "nx": VenueLink(None, None, False)}

    def test_apagado_no_se_ve(self):
        assert "BOT" not in render_status(self.links(), "live", NOW, bot="off").plain

    @pytest.mark.parametrize(
        ("status", "label"),
        [("armed", "BOT armado"), ("firing", "BOT dispara"), ("cooldown", "BOT enfría")],
    )
    def test_ocupa_el_banner_del_modo(self, status, label):
        rendered = render_status(self.links(), "live", NOW, bot=status)
        assert f" LIVE · {label} " in rendered.plain and "ÓRDENES REALES" not in rendered.plain
        span = next(s for s in rendered.spans if "LIVE" in rendered.plain[s.start : s.end])
        assert "on red" in str(span.style)  # sigue siendo el banner rojo de live

    def test_parado_en_rojo_tambien_en_dry_run(self):
        rendered = render_status(self.links(), "dry-run", NOW, bot="halted")
        assert " DRY-RUN · BOT ✖ PARADO " in rendered.plain
        span = next(s for s in rendered.spans if "PARADO" in rendered.plain[s.start : s.end])
        assert "on red" in str(span.style)

    @pytest.mark.spec("RNF-02")
    def test_el_peor_caso_sigue_cabiendo(self):
        state = ConnState("nk", "market", "rest", "down", None, NOW - 600_000)
        priv = ConnState("nk", "private", "rest", "down", None, NOW - 600_000)
        bad = VenueLink(state, NOW - 600_000, can_trade=True, private=priv)
        links = {"nk": bad, "nx": replace(bad, market=replace(state, venue="nx"))}
        for mode in ("dry-run", "live"):
            for status in ("armed", "firing", "cooldown", "halted"):
                worst = render_status(links, mode, NOW, uncertain=99, faults=99, bot=status)
                assert len(worst.plain) <= 160
