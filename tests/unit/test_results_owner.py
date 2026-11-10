"""Fills without ids are matched to the same order as before T-97 (RF-25, D-45): the
reference below is `_Builder._owner` as it was, comparing each fill with every order;
the indexed one must give the same `Report` on random journals."""

import random

import pytest

from xbt_terminal.core import results as results_module
from xbt_terminal.core.results import RECENT_MS, compute

from .test_results import T0, ack, fill, row, sent

VENUES = ("nk", "nx")
SIDES = ("buy", "sell")
PRICES = ("0.0049", "0.0050", "0.0051", "0.0052")
QTYS = ("0", "0.005", "0.01", "0.02")


class ScanBuilder(results_module._Builder):
    """The full scan of every order, as before T-97."""

    def _owner(self, row, data, venue, side, quantity, price, at):
        cid = row.get("client_id")
        if cid in self.orders:
            return cid
        oid = data.get("order_id")
        if oid and (venue, str(oid)) in self.acks:
            return self.acks[(venue, str(oid))]
        fits = [
            client_id
            for client_id, order in self.orders.items()
            if order.takes(venue, side, quantity, price, at)
        ]
        if len(fits) > 1:
            fits = [
                client_id for client_id in fits if at - self.orders[client_id].sent_at <= RECENT_MS
            ]
        return fits[0] if len(fits) == 1 else None


def random_journal(seed: int, count: int = 120) -> list[dict]:
    """Orders and fills of every kind, close in time so several orders fit one fill:
    with and without ids, partial, over-filled, rejected, re-sent with the same id,
    fills before their order and zero quantities."""
    rnd = random.Random(seed)
    rows, cids, at = [], [], T0
    for i in range(count):
        at += rnd.choice((100, 1000, 20_000, RECENT_MS + 1))
        kind = rnd.random()
        venue, side = rnd.choice(VENUES), rnd.choice(SIDES)
        if kind < 0.35 or not cids:
            cid = rnd.choice(cids) if cids and rnd.random() < 0.05 else f"c-{i}"
            double = f"d-{i // 2}" if rnd.random() < 0.8 else None
            quantity, price = rnd.choice(QTYS[1:]), rnd.choice(PRICES)
            rows.append(sent(cid, venue, side, quantity, price, double, at))
            cids.append(cid)
            if rnd.random() < 0.5:
                rows.append(ack(cid, venue, f"o-{cid}", at))
        elif kind < 0.42:
            cid = rnd.choice(cids)
            rows.append(row("order_ack", at, venue, cid, order_id=f"o-{cid}", status="rejected"))
        elif kind < 0.46:
            rows.append(row("order_error", at, venue, rnd.choice(cids), error="x"))
        else:
            quantity, price = rnd.choice(QTYS), rnd.choice(PRICES)
            ids = rnd.random()
            cid = rnd.choice(cids) if ids < 0.15 else None
            oid = f"o-{rnd.choice(cids)}" if 0.15 <= ids < 0.3 else None
            rows.append(fill(venue, side, quantity, price, cid=cid, oid=oid, at=at, tid=f"tr-{i}"))
    return rows


@pytest.mark.spec("RF-25", "RF-32")
@pytest.mark.parametrize("seed", range(40))
def test_same_report_as_the_full_scan(seed, monkeypatch):
    rows = random_journal(seed)
    got = compute(rows)
    monkeypatch.setattr(results_module, "_Builder", ScanBuilder)
    assert got == compute(rows)


@pytest.mark.spec("RF-25", "RF-32")
def test_random_journals_exercise_the_id_less_path(monkeypatch):
    """The comparison is worth something: id-less fills are assigned, left unassigned for
    having several candidates, and taken by recent orders."""
    reasons = {"one": 0, "recent": 0, "none": 0}

    class Counting(ScanBuilder):
        def _owner(self, row, data, venue, side, quantity, price, at):
            if row.get("client_id") in self.orders or data.get("order_id"):
                return super()._owner(row, data, venue, side, quantity, price, at)
            fits = [
                client_id
                for client_id, order in self.orders.items()
                if order.takes(venue, side, quantity, price, at)
            ]
            recent = [
                client_id for client_id in fits if at - self.orders[client_id].sent_at <= RECENT_MS
            ]
            if len(fits) == 1:
                reasons["one"] += 1
            elif len(fits) > 1 and len(recent) == 1:
                reasons["recent"] += 1
            else:
                reasons["none"] += 1
            return super()._owner(row, data, venue, side, quantity, price, at)

    monkeypatch.setattr(results_module, "_Builder", Counting)
    for seed in range(40):
        compute(random_journal(seed))
    assert all(count >= 20 for count in reasons.values()), reasons
