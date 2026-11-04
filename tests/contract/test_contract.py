"""The venue contract (T-88, RF-36, CA-21): the same checks for every venue, and a
broken adapter per check, because passing proves nothing if a check cannot fail."""

import inspect
import re
from pathlib import Path

import pytest

from xbt_terminal.adapters import base

from .battery import CHECKS
from .fake import MUTANTS, FakeHarness
from .venues import NeoxaHarness, NonKYCHarness

DOC = (Path(__file__).resolve().parents[2] / "docs" / "contrato-venue.md").read_text("utf-8")
HARNESSES = (NonKYCHarness, NeoxaHarness, FakeHarness)
PROTOCOLS = (
    base.MarketData,
    base.Venue,
    base.FeedUpkeep,
    base.Depositor,
    base.WithdrawalFees,
    base.TokenPricer,
)
CASES = [
    pytest.param(harness, check, id=f"{harness.name}-{check.name}")
    for harness in HARNESSES
    for check in CHECKS.values()
    if check.needs is None or check.needs in harness.implements
]


@pytest.mark.spec("RF-36", "RNF-09", "CA-21")
@pytest.mark.parametrize(("harness", "check"), CASES)
async def test_venue_honours_clause(harness, check):
    async with harness() as h:
        await check.run(h)


@pytest.mark.spec("RF-36", "CA-21")
@pytest.mark.parametrize("name", CHECKS)
async def test_broken_adapter_fails_its_clause(name):
    async with FakeHarness(MUTANTS[name]) as h:
        with pytest.raises(AssertionError):
            await CHECKS[name].run(h)


@pytest.mark.spec("RF-36", "CA-21")
def test_every_clause_has_a_broken_adapter():
    assert set(MUTANTS) == set(CHECKS)


@pytest.mark.spec("RF-36", "CA-21")
def test_document_lists_exactly_the_clauses():
    clauses = DOC.split("## Cláusulas")[1].split("## Añadir un venue")[0]
    documented = re.findall(r"^\| `([a-z_]+)` \|", clauses, re.MULTILINE)
    assert sorted(documented) == sorted(CHECKS)


@pytest.mark.spec("RF-36")
@pytest.mark.parametrize("protocol", PROTOCOLS, ids=lambda p: p.__name__)
def test_document_covers_the_protocols(protocol):
    assert f"`{protocol.__name__}`" in DOC
    members = {
        *inspect.get_annotations(protocol),
        *(n for n, _ in inspect.getmembers(protocol, callable) if not n.startswith("_")),
    }
    assert members and all(f"`{member}" in DOC for member in members)
