"""Opt-in resource-warning attribution diagnostics.

The normal test suite keeps ``filterwarnings = ["error"]`` and never forces
garbage collection. This command is the explicit opt-in for the harder
question: when a delayed ``ResourceWarning`` fires in a later test, where was
the resource actually allocated?

Usage::

    uv run python scripts/diagnose_resources.py --self-test
    uv run python scripts/diagnose_resources.py tests/test_session_lifecycle.py

It starts ``tracemalloc``, records unclosed SQLite/aiosqlite objects at each
test boundary, resolves their allocation frame to the enclosing test function,
then runs a forced collection inside a scoped ``catch_warnings`` block so a
deferred warning can be tied back to the object's address.

The global warning configuration is not modified: only the forced collection
call is wrapped, and only for ``ResourceWarning``.
"""

from __future__ import annotations

import argparse
import ast
import gc
import re
import sqlite3
import sys
import tracemalloc
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite
import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "scripts" / "diagnostics" / "test_resource_diagnostic_cases.py"
REPO_SOURCE_ROOTS = ("apps", "services", "tests", "scripts")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")

_function_cache: dict[Path, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}


@dataclass
class Origin:
    path: str
    line: int
    function: str
    test_path: str | None = None
    test_function: str | None = None

    @property
    def label(self) -> str:
        return f"{self.path}:{self.line} in {self.function}"

    @property
    def test_label(self) -> str:
        if self.test_path and self.test_function:
            return f"{self.test_path}::{self.test_function}"
        return "<unattributed>"


@dataclass
class Finding:
    address: str
    kind: str
    origin: Origin | None
    first_seen: str
    last_seen: str
    warning_during: list[str] = field(default_factory=list)

    @property
    def classification(self) -> str:
        return classify(self.origin.path if self.origin else None)


def classify(path: str | None) -> str:
    if path is None:
        return "timing-dependent observation"
    parts = Path(path).parts
    if parts and parts[0] in {"apps", "services"}:
        return "application defect"
    if parts and parts[0] in {"tests", "scripts"}:
        return "test defect"
    return "timing-dependent observation"


def relative(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return path


def _enclosing_function(path: str, line: int) -> str:
    source = Path(path).resolve()
    functions = _function_cache.get(source)
    if functions is None:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            functions = []
        else:
            functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
        _function_cache[source] = functions
    matches = [node for node in functions if node.lineno <= line <= (node.end_lineno or node.lineno)]
    if not matches:
        return "<module>"
    return max(matches, key=lambda node: node.lineno).name


def allocation_origin(obj: object) -> Origin | None:
    traceback = tracemalloc.get_object_traceback(obj)
    if traceback is None:
        return None
    candidates = []
    for frame in traceback:
        rel = relative(frame.filename)
        parts = Path(rel).parts
        if parts and parts[0] in REPO_SOURCE_ROOTS:
            candidates.append((rel, frame.lineno, _enclosing_function(rel, frame.lineno)))
    if not candidates:
        return None
    path, line, function = candidates[-1]  # most recent repository frame
    test_frame = next(
        (candidate for candidate in reversed(candidates) if candidate[2].startswith("test_")),
        None,
    )
    return Origin(
        path=path,
        line=line,
        function=function,
        test_path=test_frame[0] if test_frame else None,
        test_function=test_frame[2] if test_frame else None,
    )


def _sqlite_is_closed(connection: sqlite3.Connection) -> bool:
    try:
        _ = connection.total_changes
    except sqlite3.ProgrammingError:
        return True
    return False


def _aiosqlite_handle(connection: aiosqlite.Connection):
    # Private but stable in aiosqlite 0.22. A missing attribute is treated as
    # closed so an upgrade degrades to "no finding" rather than AttributeError.
    return getattr(connection, "_connection", None)


def unclosed_objects(objects: list[object]):
    """Yield the unclosed diagnostic resource objects, avoiding double counting.

    The ``sqlite3.Connection`` owned by an ``aiosqlite.Connection`` is skipped;
    the aiosqlite wrapper is the object whose lifecycle the tests control.
    """
    aiosqlite_connections = [obj for obj in objects if isinstance(obj, aiosqlite.Connection)]
    owned = {id(handle) for obj in aiosqlite_connections if (handle := _aiosqlite_handle(obj)) is not None}
    for obj in objects:
        if isinstance(obj, aiosqlite.Connection):
            if _aiosqlite_handle(obj) is not None:
                yield obj, "aiosqlite.Connection"
        elif isinstance(obj, sqlite3.Connection):
            if id(obj) in owned or _sqlite_is_closed(obj):
                continue
            yield obj, "sqlite3.Connection"


def _address(message: str) -> str | None:
    match = _ADDRESS.search(message)
    return match.group(0).lower() if match else None


def observe_objects() -> list[tuple[str, str, Origin | None]]:
    """Snapshot unclosed resources as primitives so collection is not blocked."""
    found = []
    for obj, kind in unclosed_objects(gc.get_objects()):
        found.append((hex(id(obj)).lower(), kind, allocation_origin(obj)))
    return found


class ResourceDiagnosticsPlugin:
    def __init__(self, expected_origin: str | None = None) -> None:
        self.expected_origin = expected_origin
        self.findings: dict[str, Finding] = {}
        self.unattributed: list[str] = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item, nextitem):
        yield
        self._observe(item.nodeid)

    def _observe(self, nodeid: str) -> None:
        for address, kind, origin in observe_objects():
            finding = self.findings.get(address)
            if finding is None:
                self.findings[address] = Finding(
                    address=address,
                    kind=kind,
                    origin=origin,
                    first_seen=nodeid,
                    last_seen=nodeid,
                )
            else:
                finding.last_seen = nodeid
                if finding.origin is None:
                    finding.origin = origin
        # Only a finding first seen in an earlier test can still be awaiting
        # collection. Collecting at every boundary would compound tracemalloc
        # cost without finding anything new.
        deferred = any(
            finding.first_seen != nodeid and not finding.warning_during for finding in self.findings.values()
        )
        if not deferred:
            return
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            gc.collect()
        for warning in caught:
            if not issubclass(warning.category, ResourceWarning):
                continue
            address = _address(str(warning.message))
            if address in self.findings:
                self.findings[address].warning_during.append(nodeid)
            else:
                self.unattributed.append(f"{nodeid}: {warning.message}")

    def pytest_sessionfinish(self, session):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = ["", "=== resource diagnostics ==="]
        lines.append(f"tracemalloc frame limit: {tracemalloc.get_traceback_limit()}")
        if not self.findings:
            lines.append("no unclosed SQLite/aiosqlite resources observed")
        for finding in sorted(self.findings.values(), key=lambda item: item.first_seen):
            origin = "<unattributed>"
            if finding.origin:
                origin = finding.origin.label
            lines.append(f"{finding.kind} at {finding.address}")
            lines.append(f"  origin: {origin}")
            lines.append("  originating test: " + (finding.origin.test_label if finding.origin else "<unattributed>"))
            lines.append(f"  classification: {finding.classification}")
            lines.append(f"  first observed during: {finding.first_seen}")
            lines.append(f"  last observed during: {finding.last_seen}")
            if finding.warning_during:
                lines.append(f"  warning emitted during: {', '.join(finding.warning_during)}")
            else:
                lines.append("  warning emitted during: not observed")
        if self.unattributed:
            lines.append("unattributed warnings (timing-dependent):")
            lines.extend(f"  {item}" for item in self.unattributed)
        lines.append("=== end resource diagnostics ===")
        output = "\n".join(lines)
        if reporter is not None:
            reporter.write_line(output)
        else:
            print(output)
        if self._self_test_failed():
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def _self_test_failed(self) -> bool:
        if self.expected_origin is None:
            return bool(self.findings)
        if len(self.findings) != 1:
            return True
        finding = next(iter(self.findings.values()))
        if finding.origin is None or finding.origin.test_function != self.expected_origin:
            return True
        if finding.classification != "test defect":
            return True
        return not any("test_2_later_collection" in nodeid for nodeid in finding.warning_during)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="pytest targets (default: bundled self-test)")
    parser.add_argument("--self-test", action="store_true", help="run the deliberate-leak cases")
    parser.add_argument("--depth", type=int, default=15, help="tracemalloc frame depth (default 15)")
    args = parser.parse_args(argv)

    self_test = args.self_test or not args.targets
    targets = [str(CASES)] if self_test else args.targets
    tracemalloc.start(args.depth)
    plugin = ResourceDiagnosticsPlugin(expected_origin="test_1_leak_origin" if self_test else None)
    try:
        code = pytest.main(["-q", "-p", "no:cacheprovider", *targets], plugins=[plugin])
    finally:
        tracemalloc.stop()
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
