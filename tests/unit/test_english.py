"""Code is in English (D-81, T-91): comments, docstrings, log messages and test
names. Text for the user stays in Spanish and lives in plain strings, out of reach.

A heuristic on purpose: accents and very common Spanish words. It catches drift;
it does not prove the absence of Spanish.
"""

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("src", "tests", "tools")
ACCENTS = re.compile(r"[áéíóúñÁÉÍÓÚÑ¿¡]")
WORDS = frozenset(
    "el la los las del que con sin por para una uno cuando pero como hay esta este "
    "ya lo se si al es son más".split()
)
LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})


def spanish(text: str) -> bool:
    words = re.findall(r"[a-záéíóúñ]+", text.lower())
    return bool(ACCENTS.search(text)) or sum(w in WORDS for w in words) >= 2


def named_spanish(name: str) -> bool:
    return spanish(name.replace("_", " "))


def comments(source: str):
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.string


def docstrings(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                yield getattr(node, "lineno", 1), doc


def test_names(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name.lower().startswith("test") and named_spanish(node.name):
                yield node.lineno, node.name


def log_messages(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "log"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            yield node.lineno, node.args[0].value


def findings(source: str):
    tree = ast.parse(source)
    found = [(n, t) for n, t in comments(source) if spanish(t)]
    found += [(n, t) for n, t in docstrings(tree) if spanish(t)]
    found += list(test_names(tree))
    found += [(n, t) for n, t in log_messages(tree) if spanish(t)]
    return found


@pytest.mark.spec("RNF-21")
def test_the_code_is_in_english():
    bad = []
    for root in ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if path.name == "test_english.py":
                continue
            bad += [f"{path.relative_to(ROOT)}:{n}: {t[:60]!r}" for n, t in findings(path.read_text())]
    assert bad == []


@pytest.mark.spec("RNF-21")
class TestDetector:
    def test_flags_a_spanish_comment(self):
        assert findings("x = 1  # lo que no llene se cancela\n")

    def test_flags_a_spanish_docstring(self):
        assert findings('def f():\n    """Devuelve el precio."""\n')

    def test_flags_a_spanish_test_name(self):
        assert findings("def test_rechaza_con_el_saldo_justo(): ...\n")

    def test_flags_a_spanish_log_message(self):
        assert findings('log.info("bot: sin disparar por movimiento brusco")\n')

    def test_leaves_user_text_alone(self):
        assert not findings('say("bot parado a mano: no dispara hasta otro bot on")\n')

    def test_leaves_english_alone(self):
        assert not findings('def f():\n    """Return the price."""  # why: see D-70\n')
