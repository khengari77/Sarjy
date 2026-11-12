"""Executable architecture rules that the import graph alone cannot express."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/api/app"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_has_no_io_or_framework_imports():
    forbidden = ("app.services", "app.infrastructure", "app.routes", "fastapi", "sqlalchemy", "httpx", "livekit")
    violations = {
        path.relative_to(ROOT): sorted(module for module in _imports(path) if module.startswith(forbidden))
        for path in (APP / "domain").glob("*.py")
    }
    assert not {path: modules for path, modules in violations.items() if modules}


def test_conversation_ownership_has_a_single_definition():
    definitions = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                "owned_conversation",
                "_owned_conversation",
            }:
                definitions.append(path.relative_to(ROOT).as_posix())
    assert definitions == ["apps/api/app/services/authorization.py"]


def test_application_factory_is_not_constructed_at_import_time():
    tree = ast.parse((APP / "main.py").read_text(encoding="utf-8"), filename="app/main.py")
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            assert node.value.func.id != "create_app", "app.main must expose a factory, not a module-level instance"
