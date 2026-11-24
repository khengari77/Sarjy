"""Select the pytest files relevant to a set of changed paths.

The selector is intentionally stdlib-only so the pre-push hook can run it
before the project environment exists.  All matching lives in pure functions;
the CLI only prints the result.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath

ALWAYS_TESTS = ("tests/test_architecture.py", "tests/test_test_configuration.py")
CONFIG_ONLY_PATHS = frozenset({"tests/conftest.py", "pyproject.toml", "uv.lock"})
ALEMBIC_CONFIG = "alembic.ini"
MODULE_TREES = ("apps/api/app", "services/voice-agent", "scripts")
EXCLUDED_TESTS = frozenset({"tests/test_web_access.py", "tests/test_web_accounts.py"})
EXCLUDED_TEST_SUFFIX = "_postgres.py"
SPECIAL_IMPORTERS = frozenset({"app.main", "main"})
WEB_UNIT_FILES = frozenset({"apps/web/package.json", "apps/web/pnpm-lock.yaml"})
WEB_UNIT_PREFIXES = ("vite.config.", "vitest.config.", "svelte.config.")
FINAL_FALLBACK_TESTS = frozenset({"tests/test_contracts.py", "tests/test_finalized_commands.py"})


@dataclass(frozen=True)
class Selection:
    """Tests and frontend sources selected for a change set."""

    tests: tuple[str, ...]
    web_files: tuple[str, ...]
    web_unit: bool = False


@dataclass(frozen=True)
class _TestFile:
    path: str
    text: str
    imports: frozenset[str] | None
    module_names: frozenset[str]


@dataclass(frozen=True)
class _Module:
    path: str
    text: str
    imports: frozenset[str]


class _TestIndex:
    def __init__(self, root: Path) -> None:
        self.files: dict[str, _TestFile] = {}
        self.test_paths: frozenset[str] = frozenset()
        self.test_importers: dict[str, set[str]] = {}
        tests_dir = root / "tests"
        if not tests_dir.is_dir():
            return
        for path in sorted(tests_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            if _is_excluded(relative):
                continue
            module_names = _test_module_names(relative)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
                imports = None
            else:
                imports = _imports_or_none(text, next(iter(module_names), ""), False)
            test = _TestFile(relative, text, imports, module_names)
            self.files[relative] = test
            if test.imports:
                for imported in test.imports:
                    self.test_importers.setdefault(imported, set()).add(relative)
        self.test_paths = frozenset(path for path in self.files if _is_test_file(path))

    def importing(self, modules: set[str]) -> set[str]:
        selected: set[str] = set()
        for path in self.test_paths:
            test = self.files[path]
            if test.imports is None or test.imports & modules:
                selected.add(path)
        return selected

    def importing_test_modules(self, modules: frozenset[str]) -> set[str]:
        seen = set(modules)
        selected: set[str] = set()
        queue = list(modules)
        while queue:
            module = queue.pop()
            for path in self.test_importers.get(module, ()):
                if path in selected:
                    continue
                selected.add(path)
                for name in self.files[path].module_names:
                    if name not in seen:
                        seen.add(name)
                        queue.append(name)
        return selected

    def importing_selected(self, selected: set[str]) -> set[str]:
        modules = frozenset(name for path in selected if path in self.files for name in self.files[path].module_names)
        return self.importing_test_modules(modules)

    def mentioning(self, needles: set[str]) -> set[str]:
        return {path for path in self.test_paths if any(needle in self.files[path].text for needle in needles)}


def _is_test_file(path: str) -> bool:
    return PurePosixPath(path).name.startswith("test_")


def _test_module_names(path: str) -> frozenset[str]:
    relative = path[len("tests/") : -len(".py")]
    parts = relative.split("/")
    if parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return frozenset()
    return frozenset({parts[-1], ".".join(parts)})


def _imports_or_none(source: str, module_name: str, is_package: bool) -> frozenset[str] | None:
    try:
        return _imported_modules(source, module_name, is_package)
    except (SyntaxError, ValueError):
        return None


@cache
def _imported_modules(source: str, module_name: str, is_package: bool) -> frozenset[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                modules.update(".".join(parts[:index]) for index in range(1, len(parts) + 1))
        elif isinstance(node, ast.ImportFrom):
            target = _import_target(node, module_name, is_package)
            if not target:
                continue
            modules.add(target)
            modules.update(f"{target}.{alias.name}" for alias in node.names if alias.name != "*")
    return frozenset(modules)


def _import_target(node: ast.ImportFrom, module_name: str, is_package: bool) -> str:
    target = node.module or ""
    if not node.level:
        return target
    base = module_name if is_package else module_name.rpartition(".")[0]
    for _ in range(node.level - 1):
        base = base.rpartition(".")[0]
    if base and target:
        return f"{base}.{target}"
    return target or base


def _normalize(path: str) -> str:
    parts = [part for part in PurePosixPath(path).parts if part not in ("", ".", "/")]
    return "/".join(parts)


def _is_excluded(path: str) -> bool:
    return path in EXCLUDED_TESTS or path.endswith(EXCLUDED_TEST_SUFFIX)


def _is_python_or_config(path: str) -> bool:
    return path.endswith(".py") or path in ("pyproject.toml", "uv.lock")


def _is_web_unit(path: str) -> bool:
    if path in WEB_UNIT_FILES:
        return True
    parts = PurePosixPath(path).parts
    return len(parts) >= 3 and parts[0] == "apps" and parts[1] == "web" and parts[-1].startswith(WEB_UNIT_PREFIXES)


def _module_name(path: str) -> str | None:
    for tree in MODULE_TREES:
        prefix = f"{tree}/"
        if not path.startswith(prefix) or not path.endswith(".py"):
            continue
        parts = path[len(prefix) : -len(".py")].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        if tree == "apps/api/app":
            return ".".join(["app", *parts])
        return ".".join(parts) if parts else None
    return None


def _module_index(root: Path) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for tree in MODULE_TREES:
        for path in sorted((root / tree).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            name = _module_name(relative)
            if name is None:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            imported = (
                frozenset() if not text else _imports_or_none(text, name, path.name == "__init__.py") or frozenset()
            )
            modules[name] = _Module(relative, text, imported)
    return modules


def _importers(modules: dict[str, _Module]) -> dict[str, set[str]]:
    importers: dict[str, set[str]] = {}
    for name, module in modules.items():
        for imported in module.imports:
            importers.setdefault(imported, set()).add(name)
    return importers


def _importer_closure(name: str, importers: dict[str, set[str]]) -> set[str]:
    closure = {name}
    queue = [name]
    while queue:
        current = queue.pop()
        for importer in importers.get(current, ()):
            if importer in SPECIAL_IMPORTERS or importer in closure:
                continue
            closure.add(importer)
            queue.append(importer)
    return closure


def _route_needles(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    needles: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/"):
            needles.add(node.value)
        elif isinstance(node, ast.JoinedStr):
            prefix = ""
            for value in node.values:
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    break
                prefix += value.value
            if prefix.startswith("/"):
                needles.add(prefix)
    return needles


def _route_tests(name: str, index: _TestIndex, importers: dict[str, set[str]], modules: dict[str, _Module]) -> set[str]:
    needles: set[str] = set()
    for importer in _importer_closure(name, importers):
        module = modules.get(importer)
        if module is not None and module.path.startswith("apps/api/app/routes/"):
            needles |= {needle for needle in _route_needles(module.text) if needle != "/"}
    if not needles:
        return set()
    return {
        test.path
        for test in index.files.values()
        if test.path in index.test_paths
        and test.imports is not None
        and ("app.main" in test.imports or "main" in test.imports)
        and any(needle in test.text for needle in needles)
    }


def _main_fallback(name: str, path: str, index: _TestIndex, modules: dict[str, _Module]) -> set[str]:
    if path.startswith("apps/api/app/"):
        main = "app.main"
    elif path.startswith("services/voice-agent/"):
        main = "main"
    else:
        return set()
    needles = {PurePosixPath(path).stem}
    module = modules.get(name)
    if module is not None:
        needles |= _route_needles(module.text)
    return {
        test.path
        for test in index.files.values()
        if test.path in index.test_paths
        and test.imports is not None
        and main in test.imports
        and any(needle in test.text for needle in needles)
    }


def _final_fallback(index: _TestIndex) -> set[str]:
    return {
        path
        for path in index.test_paths
        if path in FINAL_FALLBACK_TESTS or PurePosixPath(path).name.startswith("test_worker_")
    }


def _named_tests(path: str, index: _TestIndex) -> set[str]:
    expected = f"test_{PurePosixPath(path).stem}"
    return {
        test.path
        for test in index.files.values()
        if test.path in index.test_paths
        and (
            PurePosixPath(test.path).name == f"{expected}.py"
            or PurePosixPath(test.path).name.startswith(f"{expected}_")
        )
    }


def _is_web_source(path: str) -> bool:
    return path.startswith("apps/web/src/") and path.endswith((".ts", ".svelte"))


def _is_quality_path(path: str) -> bool:
    if path.startswith(".githooks/"):
        return True
    parts = PurePosixPath(path).parts
    return len(parts) == 2 and parts[0] == "scripts" and parts[1].endswith(".sh")


def _contract_tests(path: str, index: _TestIndex) -> set[str]:
    selected = {"tests/test_contracts.py"}
    name = PurePosixPath(path).name
    if name and name != "__init__.py":
        selected |= index.mentioning({name})
    return selected


def _module_tests(
    name: str,
    path: str,
    index: _TestIndex,
    importers: dict[str, set[str]],
    modules: dict[str, _Module],
) -> set[str]:
    selected = index.importing(_importer_closure(name, importers))
    selected |= _route_tests(name, index, importers, modules)
    selected |= _named_tests(path, index)
    if path.startswith("scripts/"):
        selected |= index.mentioning({path, PurePosixPath(path).name})
    if not (selected - set(ALWAYS_TESTS)):
        selected |= _main_fallback(name, path, index, modules)
    if not (selected - set(ALWAYS_TESTS)):
        selected |= _final_fallback(index)
    return selected


def _config_tests(path: str, index: _TestIndex) -> set[str]:
    if path in CONFIG_ONLY_PATHS:
        return set(index.test_paths)
    if path == ALEMBIC_CONFIG:
        return {"tests/test_migrations.py"}
    return set()


def _path_tests(path: str, index: _TestIndex, importers: dict[str, set[str]], modules: dict[str, _Module]) -> set[str]:
    selected: set[str] = set()
    config_tests = _config_tests(path, index)
    if config_tests:
        return config_tests
    if path.startswith("tests/"):
        if path.endswith(".py"):
            test = index.files.get(path)
            if test is not None:
                if _is_test_file(path):
                    selected.add(path)
                selected |= index.importing_test_modules(test.module_names)
        else:
            matches = index.mentioning({PurePosixPath(path).name, path})
            selected |= matches
            if not matches:
                selected.update(ALWAYS_TESTS)
        return selected
    if path.startswith("migrations/"):
        selected |= {
            test.path
            for test in index.files.values()
            if test.path in index.test_paths
            and (
                test.path == "tests/test_migrations.py"
                or (test.path.startswith("tests/test_") and test.path.endswith("_migrations.py"))
            )
        }
    if _is_quality_path(path):
        selected.add("tests/test_quality_hooks.py")
    if path.startswith("infra/"):
        selected.add("tests/test_worker_packaging.py")
    if path.startswith("packages/contracts/"):
        selected |= _contract_tests(path, index)
    name = _module_name(path)
    if name is not None:
        selected |= _module_tests(name, path, index, importers, modules)
    return selected


def select_tests(changed_paths: Iterable[str], root: Path) -> Selection:
    """Return the sorted tests and web sources for the changed repo-relative paths."""

    root = Path(root)
    changed = tuple(dict.fromkeys(_normalize(path) for path in changed_paths))
    web_files = tuple(sorted({path for path in changed if _is_web_source(path)}))
    web_unit = any(_is_web_unit(path) for path in changed)
    index = _TestIndex(root)
    modules = _module_index(root)
    importers = _importers(modules)
    selected: set[str] = set()
    for path in changed:
        selected |= _path_tests(path, index, importers, modules)
    if selected:
        selected |= index.importing_selected(selected)
    if selected or any(_is_python_or_config(path) for path in changed):
        selected.update(ALWAYS_TESTS)
    tests = tuple(sorted(test for test in selected if test in index.files and test in index.test_paths))
    return Selection(tests=tests, web_files=web_files, web_unit=web_unit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--web", action="store_true", help="print changed web sources instead of tests")
    parser.add_argument("--web-unit", action="store_true", help="print 1 when the full Vitest unit suite must run")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    selection = select_tests(args.paths, args.root)
    if args.web_unit:
        if selection.web_unit:
            print("1")
        return 0
    for path in selection.web_files if args.web else selection.tests:
        sys.stdout.buffer.write(os.fsencode(path) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
