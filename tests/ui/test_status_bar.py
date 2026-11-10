"""Status bar (T-15): connection and latency per venue and channel, and mode."""

import asyncio
from dataclasses import replace

import pytest

from xbt_terminal.core.models import ConnState
from xbt_terminal.services.market import MarketHub
from xbt_terminal.ui.panels import BookPanel, CrossPanel
from xbt_terminal.ui.status import STALE_AFTER_MS, StatusBar, VenueLink, render_status

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
    """The bar's piece for a venue."""
    return next(p for p in rendered.split("│") if venue in p)


@pytest.mark.spec("RF-14")
class TestMarketChannel:
    def test_ws_up_with_latency(self):
        nk = part(text(nk=link()).plain, "NonKYC")
        assert "ws ●" in nk and "168 ms" in nk

    def test_reconnecting_and_since_when(self):
        nk = part(text(nk=link(status="reconnecting", latency=None)).plain, "NonKYC")
        assert "reconectando 30s" in nk and " ms" not in nk

    def test_rest_fallback(self):
        nk = part(text(nk=link(source="rest", latency=240.0)).plain, "NonKYC")
        assert "respaldo ●" in nk and "240 ms" in nk

    def test_down(self):
        rendered = text(nk=link(source="rest", status="down", latency=None))
        nk = part(rendered.plain, "NonKYC")
        assert "respaldo ✖ caído 30s" in nk
        styles = [
            str(s.style) for s in rendered.spans if "caído" in rendered.plain[s.start : s.end]
        ]
        assert any("red" in state for state in styles)

    def test_no_state_yet(self):
        assert "conectando…" in part(text().plain, "Neoxa")

    def test_latency_not_measured_yet(self):
        nk = part(text(nk=link(latency=None)).plain, "NonKYC")
        assert "ws ● — ms" in nk

    def test_warns_if_state_is_not_renewed(self):
        old = link(seen=NOW - STALE_AFTER_MS - 1)
        nk = part(text(nk=old).plain, "NonKYC")
        assert "sin noticias 15s" in nk
        assert "sin noticias" not in part(text(nk=link(seen=NOW - 4_000)).plain, "NonKYC")

    def test_reconnecting_does_not_warn_of_silence(self):
        """Reconnecting has no heartbeats (D-11): the warning would only confuse."""
        old = link(status="reconnecting", latency=None, seen=NOW - STALE_AFTER_MS - 1)
        assert "sin noticias" not in part(text(nk=old).plain, "NonKYC")


def private(source="ws", status="up", since=NOW - 12_000):
    return ConnState("nk", "private", source, status, 300.0, since)


@pytest.mark.spec("RF-14", "RF-13")
class TestPrivateChannel:
    def test_no_keys(self):
        assert "privado sin keys" in part(text(nk=link(trade=False)).plain, "NonKYC")

    def test_dry_run_is_simulated(self):
        """In dry-run orders and balances are simulated (D-13): no real channel."""
        assert "privado simulado" in part(text(nk=link(trade=True), mode="dry-run").plain, "NonKYC")

    def test_live_without_channel_yet(self):
        assert "privado —" in part(text(nk=link(trade=True), mode="live").plain, "NonKYC")

    def test_ws_up(self):
        nk = replace(link(trade=True), private=private())
        assert "privado ● ws" in part(text(nk=nk, mode="live").plain, "NonKYC")

    def test_polling_up(self):
        nk = replace(link(trade=True), private=private(source="rest"))
        assert "privado ● rest" in part(text(nk=nk, mode="live").plain, "NonKYC")

    def test_down_and_since_when(self):
        nk = replace(link(trade=True), private=private(source="rest", status="down"))
        rendered = text(nk=nk, mode="live")
        assert "privado ✖ caído 12s" in part(rendered.plain, "NonKYC")


@pytest.mark.spec("RF-13")
class TestMode:
    def test_read_only(self):
        assert "SOLO LECTURA" in text().plain

    def test_dry_run(self):
        assert "DRY-RUN" in text(mode="dry-run").plain

    def test_live_red_banner(self):
        rendered = text(mode="live")
        assert "LIVE · ÓRDENES REALES" in rendered.plain
        span = next(s for s in rendered.spans if "LIVE" in rendered.plain[s.start : s.end])
        assert "on red" in str(span.style)


async def until(pilot, predicate, timeout=1.5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await pilot.pause(0.01)


@pytest.mark.spec("RF-14", "RNF-02")
class TestOnScreen:
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

    async def test_private_channel_from_the_account(self):
        from xbt_terminal.services.account import AccountService

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

    async def test_on_top_and_layout_still_fits(self):
        app = self.make()
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            bar = app.query_one(StatusBar)
            assert bar.region.y == 0 and bar.region.height == 1 and bar.region.width == 160
            assert all(p.region.height == 19 for p in app.query(BookPanel))
            cross = app.query_one(CrossPanel).region
            assert cross.y + cross.height <= 45

    async def test_reflects_hub_state(self):
        app = self.make()
        async with app.run_test(size=(160, 45)) as pilot:
            bar = app.query_one(StatusBar)
            self.feeds["nx"].push(ConnState("nx", "market", "ws", "up", 165.9, NOW))
            await until(pilot, lambda: "166 ms" in part(bar.text, "Neoxa"))
            self.feeds["nx"].push(ConnState("nx", "market", "ws", "reconnecting", None, NOW))
            await until(pilot, lambda: "reconectando" in part(bar.text, "Neoxa"))

    async def test_mode_and_keys_from_app(self):
        app = self.make(mode="dry-run", can_trade={"nk": True, "nx": False})
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            bar = app.query_one(StatusBar)
            assert "DRY-RUN" in bar.text
            assert "privado simulado" in part(bar.text, "NonKYC")
            assert "privado sin keys" in part(bar.text, "Neoxa")
            assert "modo" not in (app.query_one(CrossPanel).border_subtitle or "")

    async def test_age_advances_by_itself(self):
        app = self.make()
        async with app.run_test(size=(160, 45)) as pilot:
            bar = app.query_one(StatusBar)
            self.feeds["nk"].push(ConnState("nk", "market", "ws", "up", 100.0, NOW))
            await until(pilot, lambda: "100 ms" in part(bar.text, "NonKYC"))
            self.now = NOW + STALE_AFTER_MS + 2_000  # no new heartbeats
            await until(pilot, lambda: "sin noticias" in part(bar.text, "NonKYC"))


@pytest.mark.spec("RF-14", "RNF-02")
def test_worst_case_fits_160_columns():
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
def test_uncertain_orders(n, label):
    links = {"nk": VenueLink(None, None, False), "nx": VenueLink(None, None, False)}
    rendered = render_status(links, "live", NOW, uncertain=n).plain
    if label is None:
        assert "incierta" not in rendered
    else:
        assert label in rendered and rendered.index(label) < rendered.index("LIVE")


@pytest.mark.spec("RNF-14")
@pytest.mark.parametrize(("n", "label"), [(0, None), (1, "✖ 1 fallo "), (3, "✖ 3 fallos")])
def test_internal_faults(n, label):
    links = {"nk": VenueLink(None, None, False), "nx": VenueLink(None, None, False)}
    rendered = render_status(links, "live", NOW, faults=n).plain
    if label is None:
        assert "fallo" not in rendered
    else:
        assert label in rendered and rendered.index(label) < rendered.index("LIVE")


@pytest.mark.spec("RF-14")
async def test_state_change_drawn_on_arrival(monkeypatch):
    """Without waiting for the periodic refresh, set to 60 s here."""
    from xbt_terminal.ui import app as app_module

    monkeypatch.setattr(app_module, "STATUS_EVERY_S", 60.0)
    t = TestOnScreen()
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

    def test_off_is_not_shown(self):
        assert "BOT" not in render_status(self.links(), "live", NOW, bot="off").plain

    @pytest.mark.parametrize(
        ("status", "label"),
        [("armed", "BOT armado"), ("firing", "BOT dispara"), ("cooldown", "BOT enfría")],
    )
    def test_takes_the_mode_banner(self, status, label):
        rendered = render_status(self.links(), "live", NOW, bot=status)
        assert f" LIVE · {label} " in rendered.plain and "ÓRDENES REALES" not in rendered.plain
        span = next(s for s in rendered.spans if "LIVE" in rendered.plain[s.start : s.end])
        assert "on red" in str(span.style)  # still the live red banner

    def test_halted_red_in_dry_run_too(self):
        rendered = render_status(self.links(), "dry-run", NOW, bot="halted")
        assert " DRY-RUN · BOT ✖ PARADO " in rendered.plain
        span = next(s for s in rendered.spans if "PARADO" in rendered.plain[s.start : s.end])
        assert "on red" in str(span.style)

    @pytest.mark.spec("RNF-02")
    def test_worst_case_still_fits(self):
        state = ConnState("nk", "market", "rest", "down", None, NOW - 600_000)
        priv = ConnState("nk", "private", "rest", "down", None, NOW - 600_000)
        bad = VenueLink(state, NOW - 600_000, can_trade=True, private=priv)
        links = {"nk": bad, "nx": replace(bad, market=replace(state, venue="nx"))}
        for mode in ("dry-run", "live"):
            for status in ("armed", "firing", "cooldown", "halted"):
                worst = render_status(links, mode, NOW, uncertain=99, faults=99, bot=status)
                assert len(worst.plain) <= 160
