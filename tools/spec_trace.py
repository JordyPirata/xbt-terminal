"""Matriz de trazabilidad: requerimientos de la spec contra tests marcados.

    uv run python tools/spec_trace.py            # informe
    uv run python tools/spec_trace.py --strict   # falla si algún RF/RNF/CA no tiene test

Lee los IDs de las tablas de requirements.md (filas que empiezan por `| RF-`,
`| RNF-` o `| CA-`) y las marcas `@pytest.mark.spec("RF-10", ...)` de tests/.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ID_ROW = re.compile(r"^\|\s*((?:RF|RNF|CA)-\d{2})\s*\|", re.MULTILINE)


def spec_ids(requirements: Path) -> list[str]:
    return ID_ROW.findall(requirements.read_text(encoding="utf-8"))


def _is_spec_mark(node: ast.expr) -> bool:
    # pytest.mark.spec(...)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "spec"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "mark"
    )


def test_marks(tests_dir: Path) -> dict[str, list[str]]:
    """ID -> ['tests/unit/test_x.py::test_y', ...]"""
    found: dict[str, list[str]] = {}
    for path in sorted(tests_dir.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            for deco in node.decorator_list:
                if not _is_spec_mark(deco):
                    continue
                for arg in deco.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.setdefault(arg.value, []).append(f"{rel}::{node.name}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", default="specs/mvp/requirements.md")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    ids = spec_ids(ROOT / args.spec)
    marks = test_marks(ROOT / "tests")

    missing = [i for i in ids if i not in marks]
    unknown = sorted(set(marks) - set(ids))

    width = max(map(len, ids), default=6)
    for i in ids:
        tests = marks.get(i, [])
        status = f"{len(tests)} test(s)" if tests else "SIN TEST"
        print(f"{i:<{width}}  {status}")
    print(f"\ncubiertos {len(ids) - len(missing)}/{len(ids)}")

    if unknown:
        print(f"IDs en tests que no existen en la spec: {', '.join(unknown)}")
    if unknown or (args.strict and missing):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
