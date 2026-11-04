"""RNF-21: each change passes CI with the same steps run when closing a task."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"

# In the order they must run: without a frozen lockfile, the rest is worthless.
STEPS = [
    "uv sync --frozen",
    "uv run pytest",
    "uv run ruff check .",
    "uv run python tools/spec_trace.py",
]


@pytest.mark.spec("RNF-21")
def test_workflow_runs_the_four_steps_in_order():
    text = WORKFLOW.read_text(encoding="utf-8")
    positions = [text.find(step) for step in STEPS]
    assert -1 not in positions, dict(zip(STEPS, positions, strict=True))
    assert positions == sorted(positions)


@pytest.mark.spec("RNF-21")
def test_workflow_runs_on_each_push_and_pr():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "push:" in text
    assert "pull_request:" in text
