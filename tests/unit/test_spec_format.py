"""La spec es la fuente de verdad: si su formato se rompe, spec_trace.py deja de ver IDs."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import spec_trace  # noqa: E402


def test_requirements_define_all_ids():
    ids = spec_trace.spec_ids(ROOT / "specs/mvp/requirements.md")
    assert [i for i in ids if i.startswith("RF-")] == [f"RF-{n:02d}" for n in range(1, 33)]
    assert [i for i in ids if i.startswith("RNF-")] == [f"RNF-{n:02d}" for n in range(1, 24)]
    assert [i for i in ids if i.startswith("CA-")] == [f"CA-{n:02d}" for n in range(1, 18)]
