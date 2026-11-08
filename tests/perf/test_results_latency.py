"""Results latency (T-97, RF-25): `compute` over a journal the size of ten days of the
live bot. Before T-97 each id-less Neoxa fill was compared with every order, and this
took ~0.5 s; the budget leaves margin for a loaded suite. It prints what it measured
(see it with `pytest -s`)."""

import pytest

from xbt_terminal.core.results import compute

from ..unit.test_results import DAY, T0, plain_double
from .test_bot_latency import median_ms

DOUBLES = 1500
BUDGET_MS = 150.0


def journal() -> list[dict]:
    rows = []
    for i in range(DOUBLES):
        rows += plain_double(f"d-{i}", T0 - 10 * DAY + i * 500_000)
    return rows


@pytest.mark.spec("RF-25", "RF-32")
def test_compute_ten_days_within_budget():
    rows = journal()
    report = compute(rows)
    assert report.summary.closed == DOUBLES
    took = median_ms(lambda: compute(rows), runs=5)
    print(f"\ncompute with {DOUBLES} doubles: {took:.0f} ms")
    assert took < BUDGET_MS
