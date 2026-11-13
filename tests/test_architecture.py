"""Executable architecture rules that the import graph alone cannot express."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/api/app"
FORBIDDEN_DOMAIN_IMPORTS = (
    "app.services",
    "app.infrastructure",
    "app.routes",
    "fastapi",
    "sqlalchemy",
    "httpx",
    "livekit",
)


def _imports(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def forbidden_imports(source: str, forbidden: tuple[str, ...] = FORBIDDEN_DOMAIN_IMPORTS) -> list[str]:
    return sorted(module for module in _imports(source) if module.startswith(forbidden))


def dialect_imports(source: str) -> list[str]:
    return sorted(module for module in _imports(source) if module.startswith("sqlalchemy.dialects"))


def ownership_definitions(source: str) -> list[str]:
    tree = ast.parse(source)
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"owned_conversation", "_owned_conversation"}
    ]


def uses_setattr(source: str) -> bool:
    return "object.__setattr__" in source


def constructs_factory(source: str) -> bool:
    tree = ast.parse(source)
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "create_app"
        ):
            return True
    return False


def test_domain_has_no_io_or_framework_imports():
    violations = {
        path.relative_to(ROOT): forbidden_imports(path.read_text(encoding="utf-8"))
        for path in (APP / "domain").glob("*.py")
    }
    assert not {path: modules for path, modules in violations.items() if modules}


def test_dialect_specific_imports_live_in_infrastructure():
    violations = [
        path.relative_to(ROOT).as_posix()
        for path in APP.rglob("*.py")
        if "infrastructure" not in path.parts and dialect_imports(path.read_text(encoding="utf-8"))
    ]
    assert not violations


def test_conversation_ownership_has_a_single_definition():
    definitions = [
        path.relative_to(ROOT).as_posix()
        for path in APP.rglob("*.py")
        if ownership_definitions(path.read_text(encoding="utf-8"))
    ]
    assert definitions == ["apps/api/app/services/authorization.py"]


def test_domain_never_uses_setattr_escape_hatches():
    violations = [
        path.relative_to(ROOT).as_posix()
        for path in (APP / "domain").glob("*.py")
        if uses_setattr(path.read_text(encoding="utf-8"))
    ]
    assert not violations


def test_application_factory_is_not_constructed_at_import_time():
    assert not constructs_factory((APP / "main.py").read_text(encoding="utf-8"))


def test_checkers_reject_invalid_snippets_in_memory():
    assert forbidden_imports("from sqlalchemy import select") == ["sqlalchemy"]
    assert forbidden_imports("from app.domain.contracts import ModelRequest") == []
    assert dialect_imports("from sqlalchemy.dialects.sqlite import insert") == ["sqlalchemy.dialects.sqlite"]
    assert dialect_imports("from app.infrastructure.upsert import insert_ignore") == []
    assert ownership_definitions("async def _owned_conversation():\n    pass\n") == ["_owned_conversation"]
    assert ownership_definitions("async def unrelated():\n    pass\n") == []
    assert uses_setattr("object.__setattr__(model, 'x', 1)")
    assert not uses_setattr("model.model_copy(update={'x': 1})")
    assert constructs_factory("app = create_app()")
    assert not constructs_factory("def create_app():\n    return None\n")
