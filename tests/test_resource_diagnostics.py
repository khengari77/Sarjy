"""Unit coverage for the opt-in resource diagnostics helper."""

import asyncio
import importlib.util
import sqlite3
import sys
from pathlib import Path

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_resources.py"
CASES = ROOT / "scripts" / "diagnostics" / "test_resource_diagnostic_cases.py"

_spec = importlib.util.spec_from_file_location("diagnose_resources", SCRIPT)
assert _spec is not None and _spec.loader is not None
diagnostics = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = diagnostics
_spec.loader.exec_module(diagnostics)


def _line_containing(path: Path, needle: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not found in {path}")


def test_closed_sqlite_connections_are_not_reported(tmp_path):
    connection = sqlite3.connect(tmp_path / "closed.db")
    assert [kind for _, kind in diagnostics.unclosed_objects([connection])] == ["sqlite3.Connection"]
    connection.close()
    assert list(diagnostics.unclosed_objects([connection])) == []


def test_closed_aiosqlite_connection_is_not_reported(tmp_path):
    async def run():
        connection = await aiosqlite.connect(tmp_path / "closed.db")
        assert [kind for _, kind in diagnostics.unclosed_objects([connection])] == ["aiosqlite.Connection"]
        await connection.close()
        # The owned sqlite3 connection is skipped rather than double counted.
        assert list(diagnostics.unclosed_objects([connection])) == []

    asyncio.run(run())


def test_enclosing_function_resolves_the_originating_test():
    line = _line_containing(CASES, "connection = aiosqlite.connect(")
    assert diagnostics._enclosing_function(str(CASES), line) == "test_1_leak_origin"


def test_classification_separates_application_test_and_unattributed():
    assert diagnostics.classify("apps/api/app/main.py") == "application defect"
    assert diagnostics.classify("services/voice-agent/generation.py") == "application defect"
    assert diagnostics.classify("tests/test_session_lifecycle.py") == "test defect"
    assert diagnostics.classify("scripts/diagnostics/test_resource_diagnostic_cases.py") == "test defect"
    assert diagnostics.classify(None) == "timing-dependent observation"


def test_warning_address_is_normalized():
    assert diagnostics._address("unclosed database in <object at 0xAbC123>") == "0xabc123"
    assert diagnostics._address("no address here") is None
