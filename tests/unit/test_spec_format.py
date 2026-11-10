"""The spec is the source of truth: if its format breaks, spec_trace.py stops seeing IDs."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import spec_trace  # noqa: E402


def test_requirements_define_all_ids():
    ids = spec_trace.spec_ids(ROOT / "specs/mvp/requirements.md")
    assert [i for i in ids if i.startswith("RF-")] == [
        f"RF-{number:02d}" for number in range(1, 37)
    ]
    assert [i for i in ids if i.startswith("RNF-")] == [
        f"RNF-{number:02d}" for number in range(1, 25)
    ]
    assert [i for i in ids if i.startswith("CA-")] == [
        f"CA-{number:02d}" for number in range(1, 22)
    ]
