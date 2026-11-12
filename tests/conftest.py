"""Required integration CI must execute every selected acceptance case."""
import os

import pytest

_skipped_required = []


def pytest_configure(config):
    if os.environ.get('REQUIRE_DAY2_INTEGRATION') == '1':
        missing = [name for name in ('TEST_POSTGRES_URL', 'TEST_MEMORY_POSTGRES_URL') if not os.environ.get(name)]
        if missing:
            raise pytest.UsageError('Required disposable database URLs missing: ' + ', '.join(missing))


def pytest_runtest_logreport(report):
    if os.environ.get('REQUIRE_DAY2_INTEGRATION') == '1' and report.skipped:
        _skipped_required.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if _skipped_required:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin('terminalreporter')
        if reporter:
            reporter.write_sep('=', 'Required integration checks skipped: ' + ', '.join(_skipped_required))
