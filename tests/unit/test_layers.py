"""Reglas de importación entre capas (design.md §2, T-46, D-54): core solo
importa core, adapters no importa services ni ui, y ui no importa adapters."""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "src/xbt_terminal"


def imported_layers(layer: str) -> dict[str, set[str]]:
    """Por fichero de la capa, las capas del proyecto que importa."""
    out: dict[str, set[str]] = {}
    for path in sorted((PKG / layer).glob("*.py")):
        found: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                if node.level == 1:
                    found.add(layer)
                else:  # `from ..x import y` o `from .. import x`
                    names = [node.module] if node.module else [a.name for a in node.names]
                    found.update(n.split(".")[0] for n in names)
            elif isinstance(node, ast.ImportFrom | ast.Import):
                mods = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                )
                for mod in mods:
                    if mod and mod.startswith("xbt_terminal."):
                        found.add(mod.split(".")[1])
        out[path.name] = found
    return out


@pytest.mark.spec("RNF-09", "RNF-22")
def test_core_solo_importa_core():
    bad = {f: deps - {"core"} for f, deps in imported_layers("core").items() if deps - {"core"}}
    assert bad == {}


@pytest.mark.spec("RNF-09")
def test_adapters_no_importa_services_ni_ui():
    bad = {
        f: deps & {"services", "ui"}
        for f, deps in imported_layers("adapters").items()
        if deps & {"services", "ui"}
    }
    assert bad == {}


@pytest.mark.spec("RNF-09")
def test_ui_no_importa_adapters():
    bad = {f for f, deps in imported_layers("ui").items() if "adapters" in deps}
    assert bad == set()


def services_imported(layer: str) -> dict[str, set[str]]:
    """Por fichero de la capa, los módulos de `services` que importa."""
    out: dict[str, set[str]] = {}
    for path in sorted((PKG / layer).glob("*.py")):
        found: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if node.level == 2 and parts[0] == "services":
                    found.add(parts[1] if len(parts) > 1 else "*")
                elif parts[:2] == ["xbt_terminal", "services"]:
                    found.add(parts[2] if len(parts) > 2 else "*")
        out[path.name] = found
    return out


@pytest.mark.spec("RF-31")
def test_ui_solo_importa_el_cliente_del_daemon():
    """D-66: la pantalla es el cliente; de `services` solo la cara remota y el
    formato del socket, nunca lo que habla con los venues."""
    bad = {
        f: deps - {"remote", "wire"}
        for f, deps in services_imported("ui").items()
        if deps - {"remote", "wire"}
    }
    assert bad == {}
