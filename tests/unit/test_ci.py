"""RNF-21: cada cambio pasa por CI con los mismos pasos que se corren al cerrar una tarea."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"

# En el orden en que deben correr: sin lockfile congelado, el resto no vale.
STEPS = [
    "uv sync --frozen",
    "uv run pytest",
    "uv run ruff check .",
    "uv run python tools/spec_trace.py",
]


@pytest.mark.spec("RNF-21")
def test_el_workflow_ejecuta_los_cuatro_pasos_en_orden():
    text = WORKFLOW.read_text(encoding="utf-8")
    positions = [text.find(step) for step in STEPS]
    assert -1 not in positions, dict(zip(STEPS, positions, strict=True))
    assert positions == sorted(positions)


@pytest.mark.spec("RNF-21")
def test_el_workflow_corre_en_cada_push_y_pr():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "push:" in text
    assert "pull_request:" in text
