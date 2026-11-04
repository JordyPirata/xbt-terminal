"""Replay on simulated venues and report (T-87, RF-34, RF-35, CA-19, CA-20): the real
engine runs on a recording, in virtual time."""

import asyncio
import json
import textwrap
from contextlib import aclosing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from xbt_terminal.__main__ import build_engine, replay_report, replay_venues
from xbt_terminal.config import load_settings
from xbt_terminal.core.models import BookSnapshot, ConnState, D, Level, OrderRequest, Trade
from xbt_terminal.services.control import ControlServer
from xbt_terminal.services.recording import read_recording, scan_recording
from xbt_terminal.services.replay import SimVenue, VirtualLoop
from xbt_terminal.services.wire import dumps

from ..fakes import T0, spec

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.toml"
BOT = """
[bot]
enabled = false
min_net_pct = "0.4"
max_slippage_pct = "0"
max_qty = "1"
fill_timeout_ms = 500
cooldown_s = 2
max_trades_per_hour = 100
max_daily_btc = "1"
max_consecutive_failures = 3
lead_venue = "nx"
"""


def config(tmp_path, *, competitor="0", bot=BOT):
    text = EXAMPLE.read_text()
    text = text[: text.index("[bot]")] + bot
    text = text.replace('max_venue_exposure     = "0.01"', 'max_venue_exposure     = "100"')
    text += textwrap.dedent(f"""
        [replay]
        base_balance = "10"
        quote_balance = "1"
        competitor_pct = "{competitor}"
        seed = 7
        """)
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def snap(venue, bid, ask, t, qty="1"):
    return BookSnapshot(venue, (Level(D(bid), D(qty)),), (Level(D(ask), D(qty)),), t, None)


def line(t, venue, kind, event):
    return dumps({"t": t, "venue": venue, "kind": kind, "data": event}).decode()


QUIET = {"nk": ("0.0100", "0.0102"), "nx": ("0.0099", "0.0100")}


def scenario(path):
    """Four crossings buying on Neoxa and selling on NonKYC: a good one, one vanishing
    before the order arrives (`bot_miss`), one where the other leg falls (hanging) and
    another good one."""
    rows = ['{"format":1}\n']
    for vid in ("nk", "nx"):
        rows.append(line(T0, vid, "market", spec(vid)))
        rows.append(line(T0, vid, "conn", ConnState(vid, "market", "ws", "up", None, T0)))
        rows.append(line(T0, vid, "book", snap(vid, *QUIET[vid], T0)))

    def at(s, vid, bid, ask, qty="1"):
        t = T0 + int(s * 1000)
        rows.append(line(t, vid, "book", snap(vid, bid, ask, t, qty)))

    cross, calm = ("0.0095", "0.0096"), QUIET["nx"]
    at(10, "nx", *cross)  # good
    at(11, "nx", *calm)
    at(12, "nk", *QUIET["nk"], qty="2")  # the bid the bot took is replenished
    at(30, "nx", *cross)  # gone before the order arrives
    at(30.1, "nx", *calm)
    at(40, "nx", *cross)  # fills Neoxa; NonKYC falls before the second leg arrives
    at(40.4, "nk", "0.0090", "0.0102")
    at(41, "nx", *calm)
    at(50, "nk", *QUIET["nk"])
    at(60, "nx", *cross)  # good
    at(61, "nx", *calm)
    rows.append(line(T0 + 90_000, "nx", "trade", Trade("nx", D("0.0100"), D("1"), "buy", T0)))
    path.write_text("".join(rows))
    return path


@pytest.mark.spec("RF-34", "CA-19")
class TestVirtualLoop:
    def test_time_jumps_without_waiting(self):
        loop = VirtualLoop(1000.0)
        try:
            loop.run_until_complete(asyncio.sleep(3600))
            assert 4600.0 <= loop.time() < 4600.1
            assert loop.now_ms() == 4_600_000
        finally:
            loop.close()


def venues_on_virtual(tmp_path, recording, competitor="0"):
    settings = load_settings(config(tmp_path, competitor=competitor), tmp_path / "no.env",
                             False, replay=True)  # fmt: skip
    info = scan_recording(recording)
    loop = VirtualLoop(info.first / 1000)
    return loop, settings, replay_venues(recording, info, loop.now_ms)


@pytest.mark.spec("RF-34", "CA-19")
class TestReplayStream:
    def test_same_events_in_order_with_their_times(self, tmp_path):
        recording = scenario(tmp_path / "r.jsonl")
        loop, _, feeds = venues_on_virtual(tmp_path, recording)
        got = {vid: [] for vid in feeds}

        async def drain(vid):
            async with aclosing(feeds[vid].market_stream()) as stream:
                async for event in stream:
                    got[vid].append((loop.now_ms(), event))
                    if isinstance(event, ConnState) and event.status == "down":
                        return

        async def both():
            await asyncio.gather(*(drain(v) for v in feeds))

        try:
            loop.run_until_complete(both())
        finally:
            loop.close()
        for vid in feeds:
            want = [
                (r.t, r.event)
                for r in read_recording(recording)
                if r.venue == vid and r.event != spec(vid)
            ]
            assert got[vid][:-1] == want
            assert got[vid][-1][1].status == "down"
            assert got[vid][-1][0] == T0 + 90_000  # both, when the recording ends

    def test_another_origin_shifts_times_equally(self, tmp_path):
        recording = scenario(tmp_path / "r.jsonl")
        info = scan_recording(recording)
        loop = VirtualLoop((info.first + 1_000_000) / 1000)
        feeds = replay_venues(recording, info, loop.now_ms)

        async def first_cross():
            async with aclosing(feeds["nx"].market_stream()) as stream:
                async for event in stream:
                    if isinstance(event, BookSnapshot) and event.asks[0].price == D("0.0096"):
                        return loop.now_ms(), event.ts

        try:
            at, ts = loop.run_until_complete(first_cross())
        finally:
            loop.close()
        assert at == ts == T0 + 10_000 + 1_000_000

    async def test_rest_book_is_the_replayed_one(self, tmp_path):
        recording = scenario(tmp_path / "r.jsonl")
        feeds = replay_venues(recording, scan_recording(recording), lambda: T0)
        stream = feeds["nk"].market_stream()
        async with aclosing(stream):
            while not feeds["nk"].book.valid:
                await anext(stream)
            rest = await feeds["nk"].book_snapshot(5)
        assert rest.asks[0].price == D("0.0102")
        assert await feeds["nk"].candles("1m", 10) == []
        assert await feeds["nk"].recent_trades(10) == []
        assert await feeds["nk"].market() == spec("nk")


async def sim_on(tmp_path, books, *, competitor="0", latency=0):
    """A SimVenue with the `books` book already loaded."""
    recording = tmp_path / "r.jsonl"
    rows = ['{"format":1}\n']
    for vid in ("nk", "nx"):
        rows.append(line(T0, vid, "market", spec(vid)))
    rows.append(line(T0, "nk", "book", books))
    recording.write_text("".join(rows))
    settings = load_settings(config(tmp_path, competitor=competitor), tmp_path / "no.env",
                             False, replay=True)  # fmt: skip
    feeds = replay_venues(recording, scan_recording(recording), lambda: T0)
    stream = feeds["nk"].market_stream()
    await anext(stream)
    await stream.aclose()
    cfg = replace(settings.config.replay, latency_ms=latency)
    return SimVenue(feeds["nk"], cfg, clock=lambda: T0)


DEEP = BookSnapshot(
    "nk",
    (Level(D("0.0098"), D("1")),),
    (Level(D("0.0100"), D("1")), Level(D("0.0101"), D("1")), Level(D("0.0105"), D("5"))),
    T0,
)


def buy(qty, price):
    return OrderRequest("nk", "buy", D(qty), D(price), "t-1")


@pytest.mark.spec("RF-34", "CA-19")
class TestSimVenue:
    async def test_fills_by_levels_with_taker_fee_and_returns_the_excess(self, tmp_path):
        sim = await sim_on(tmp_path, DEEP)
        order = await sim.place_limit(buy("2", "0.0102"))
        assert order.status == "filled" and order.filled == D(2)
        balances = await sim.balances()
        paid = (D("0.0100") + D("0.0101")) * (1 + D("0.002"))
        assert balances["BTC"].available == D(1) - paid
        assert balances["BTC"].locked == 0
        assert balances["BTCB2"].available == D(12)

    async def test_unmatched_stays_open_until_cancelled(self, tmp_path):
        sim = await sim_on(tmp_path, DEEP)
        order = await sim.place_limit(buy("3", "0.0101"))
        assert order.status == "partial" and order.filled == D(2)
        await sim.cancel(order.order_id)
        balances = await sim.balances()
        assert balances["BTC"].locked == 0
        assert not await sim.open_orders()

    async def test_competitor_takes_it_all(self, tmp_path):
        sim = await sim_on(tmp_path, DEEP, competitor="100")
        order = await sim.place_limit(buy("1", "0.0102"))
        assert order.status == "open" and order.filled == 0

    async def test_same_seed_gives_the_same(self, tmp_path):
        async def fills():
            sim = await sim_on(tmp_path, DEEP, competitor="50")
            return [(await sim.place_limit(buy("1", "0.0100"))).filled for _ in range(8)]

        first, second = await fills(), await fills()
        assert first == second
        assert len(set(first)) == 2  # with half the levels, some fill and others do not

    async def test_matched_does_not_match_again(self, tmp_path):
        sim = await sim_on(tmp_path, DEEP)
        assert (await sim.place_limit(buy("1", "0.0100"))).filled == D(1)
        assert (await sim.place_limit(buy("1", "0.0100"))).filled == 0

    async def test_spent_forgotten_if_recording_moves_level(self, tmp_path):
        sim = await sim_on(tmp_path, DEEP)
        await sim.place_limit(buy("0.5", "0.0100"))
        assert (await sim.place_limit(buy("1", "0.0100"))).filled == D("0.5")
        moved = BookSnapshot("nk", DEEP.bids, (Level(D("0.0100"), D("2")),), T0 + 1)
        sim._feed.apply(moved)
        assert (await sim.place_limit(buy("2", "0.0100"))).filled == D(2)

    async def test_insufficient_balance_rejected_as_in_dry_run(self, tmp_path):
        sim = await sim_on(tmp_path, DEEP)
        with pytest.raises(Exception, match="saldo insuficiente"):
            await sim.place_limit(buy("500", "0.0105"))

    async def test_order_arrives_after_latency(self, tmp_path):
        sim = await sim_on(tmp_path, DEEP, latency=5)
        order = await asyncio.wait_for(sim.place_limit(buy("1", "0.0100")), 1)
        assert order.status == "filled"


@pytest.mark.spec("RF-34", "RF-18", "CA-19")
async def test_replay_daemon_starts_without_keys_and_client_sees_book(tmp_path):
    recording = scenario(tmp_path / "r.jsonl")
    settings = load_settings(config(tmp_path), tmp_path / "no.env", False, replay=True)
    feeds = replay_venues(recording, scan_recording(recording))
    engine = build_engine(settings, logs=tmp_path / "datos", venues=feeds)
    assert engine.mode == "dry-run" and engine.bot is not None
    assert engine.bot.state.status == "off"
    assert engine.orders.can_trade("nk") and engine.orders.can_trade("nx")
    (tmp_path / "datos").mkdir()
    server = ControlServer(engine, tmp_path / "datos" / "s.sock")
    await server.start()
    tasks = [asyncio.create_task(work()) for _, work in engine.services()]
    try:
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "datos" / "s.sock"))
        kinds = set()
        async with asyncio.timeout(3):
            while "book" not in kinds:
                kinds.add(json.loads(await reader.readline()).get("type"))
        writer.close()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.aclose()
        await engine.aclose()


@pytest.mark.spec("RF-35", "CA-20")
class TestReport:
    def test_gives_expected_twice_the_same_and_leaves_nothing(self, tmp_path):
        recording = scenario(tmp_path / "r.jsonl")
        settings = load_settings(config(tmp_path), tmp_path / "no.env", False, replay=True)
        before = sorted(p.name for p in tmp_path.iterdir())
        first = replay_report(settings, recording)
        second = replay_report(settings, recording)
        assert first == second
        assert (first.crossings, first.fires, first.filled) == (4, 4, 2)
        assert (first.misses, first.hanging, first.halted) == (1, 1, None)
        assert first.capture_pct == D(50)
        assert first.miss_per_100 == first.hanging_per_100 == D(25)
        assert first.minutes == D("1.5")
        # 2 doubles of 1 XBT: buy at 0.0096 and sell at 0.0100, 0.2 % on each
        each = D("0.0100") - D("0.0096") - (D("0.0100") + D("0.0096")) * D("0.002")
        assert first.net == 2 * each
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_total_competitor_fills_nothing(self, tmp_path):
        recording = scenario(tmp_path / "r.jsonl")
        settings = load_settings(
            config(tmp_path, competitor="100"), tmp_path / "no.env", False, replay=True
        )
        report = replay_report(settings, recording)
        assert report.filled == 0 and report.net == Decimal(0)
        assert report.misses == report.fires == 4

    def test_long_cross_makes_no_extra_money(self, tmp_path):
        """The matched level stays spent: firing again at the same cross fails."""
        recording = tmp_path / "r.jsonl"
        text = scenario(recording).read_text().replace(f'"t":{T0 + 11_000}', f'"t":{T0 + 16_000}')
        recording.write_text(text)
        settings = load_settings(config(tmp_path), tmp_path / "no.env", False, replay=True)
        report = replay_report(settings, recording)
        assert report.fires > 4
        assert report.filled == 2


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replay"


@pytest.mark.spec("RF-35", "CA-20", "RF-34")
def test_report_on_fixed_real_sample(tmp_path):
    """7 min of real books with the demo config: two thin crossings. The first fills; the
    second hangs, because NonKYC's ask is still spent from the first."""
    settings = load_settings(FIXTURES / "config.toml", tmp_path / "no.env", False, replay=True)
    report = replay_report(settings, FIXTURES / "muestra.jsonl")
    assert (report.crossings, report.fires, report.filled) == (2, 2, 1)
    assert (report.misses, report.hanging, report.halted) == (0, 1, None)
    assert round(report.net * 100_000_000) == 8
    assert report == replay_report(settings, FIXTURES / "muestra.jsonl")

