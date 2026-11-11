"""Names say what they hold (D-83, T-98): no single-letter locals, parameters or
module names, so a reader does not need a comment to know what a variable is.

The allowed letters are the conventions of the codebase: `_` (discarded), indices
`i`, `j`, `k`, the `Decimal` alias `D` and the `TypeVar` `T`. Class-level names are
out of reach on purpose: dataclass fields are part of the wire and journal formats
(D-83). The frozen oracle keeps its names as they were (its own header says so).
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("src", "tests", "tools")
ALLOWED = frozenset({"_", "i", "j", "k", "D", "T"})
FROZEN = frozenset({"tests/unit/bot_reference.py"})
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ListComp, ast.SetComp,
          ast.DictComp, ast.GeneratorExp)


def single_letter_names(source: str) -> list[tuple[int, str]]:
    """(line, name) of every bare single-letter name bound or read outside a class body."""
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, in_class_body: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, True)
                continue
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, False)
                continue
            if in_class_body:
                visit(child, True)
                continue
            name = child.id if isinstance(child, ast.Name) else (
                child.arg if isinstance(child, ast.arg) else None
            )
            if name is not None and len(name) == 1 and name not in ALLOWED:
                found.append((child.lineno, name))
            visit(child, False)

    visit(ast.parse(source), False)
    return found


def python_files():
    for top in ROOTS:
        for path in sorted((ROOT / top).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


@pytest.mark.spec("RNF-21")
def test_no_single_letter_locals_or_parameters():
    offenders = []
    for path in python_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in FROZEN:
            continue
        for line, name in single_letter_names(path.read_text(encoding="utf-8")):
            offenders.append(f"{relative}:{line}: {name}")
    assert not offenders, "\n".join(offenders[:40])


@pytest.mark.spec("RNF-21")
def test_the_check_catches_a_single_letter_parameter():
    assert single_letter_names("def f(x):\n    return x\n") == [(1, "x"), (2, "x")]


@pytest.mark.spec("RNF-21")
def test_the_check_allows_conventions_and_class_fields():
    source = "class Row:\n    t: int\n\ndef f(i, D=None):\n    return [i for i in range(D)]\n"
    assert single_letter_names(source) == []
