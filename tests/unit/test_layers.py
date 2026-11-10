"""Import rules between layers (design.md §2, T-46, D-54): core imports only core,
adapters import neither services nor ui, and ui does not import adapters."""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "src/xbt_terminal"


def imported_layers(layer: str) -> dict[str, set[str]]:
    """For each file of the layer, the project layers it imports."""
    out: dict[str, set[str]] = {}
    for path in sorted((PKG / layer).glob("*.py")):
        found: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                if node.level == 1:
                    found.add(layer)
                else:  # `from ..x import y` or `from .. import x`
                    names = [node.module] if node.module else [alias.name for alias in node.names]
                    found.update(module_name.split(".")[0] for module_name in names)
            elif isinstance(node, ast.ImportFrom | ast.Import):
                mods = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [alias.name for alias in node.names]
                )
                for mod in mods:
                    if mod and mod.startswith("xbt_terminal."):
                        found.add(mod.split(".")[1])
        out[path.name] = found
    return out


@pytest.mark.spec("RNF-09", "RNF-22")
def test_core_only_imports_core():
    bad = {
        file_path: deps - {"core"}
        for file_path, deps in imported_layers("core").items()
        if deps - {"core"}
    }
    assert bad == {}


@pytest.mark.spec("RNF-09")
def test_adapters_import_neither_services_nor_ui():
    bad = {
        file_path: deps & {"services", "ui"}
        for file_path, deps in imported_layers("adapters").items()
        if deps & {"services", "ui"}
    }
    assert bad == {}


@pytest.mark.spec("RNF-09")
def test_ui_does_not_import_adapters():
    bad = {file_path for file_path, deps in imported_layers("ui").items() if "adapters" in deps}
    assert bad == set()


def services_imported(layer: str) -> dict[str, set[str]]:
    """For each file of the layer, the `services` modules it imports."""
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
def test_ui_only_imports_daemon_client():
    """D-66: the screen is the client; from `services` only the remote face and the socket
    format, never what talks to venues."""
    bad = {
        file_path: deps - {"remote", "wire"}
        for file_path, deps in services_imported("ui").items()
        if deps - {"remote", "wire"}
    }
    assert bad == {}
