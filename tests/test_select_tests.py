"""Cover the pure test-selection rules used by the pre-push hook."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("select_tests", ROOT / "scripts" / "select_tests.py")
assert _SPEC is not None and _SPEC.loader is not None
select_tests = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = select_tests
_SPEC.loader.exec_module(select_tests)


def _write(root: Path, path: str, text: str = "") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _write_bytes(root: Path, path: str, data: bytes) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _select(root: Path, *paths: str) -> select_tests.Selection:
    return select_tests.select_tests(paths, root)


def _tests(root: Path, *paths: str) -> tuple[str, ...]:
    return _select(root, *paths).tests


@pytest.fixture(scope="module", autouse=True)
def _reuse_real_tree_index():
    """Build the repository index once for the many cases that select against ROOT."""
    index = select_tests._TestIndex(ROOT)
    modules = select_tests._module_index(ROOT)
    original_index = select_tests._TestIndex
    original_module_index = select_tests._module_index

    def cached_index(root: Path):
        return index if Path(root) == ROOT else original_index(root)

    def cached_module_index(root: Path):
        return modules if Path(root) == ROOT else original_module_index(root)

    select_tests._TestIndex = cached_index
    select_tests._module_index = cached_module_index
    try:
        yield
    finally:
        select_tests._TestIndex = original_index
        select_tests._module_index = original_module_index


def test_changed_test_selects_itself(tmp_path):
    _write(tmp_path, "tests/test_alpha.py")
    assert _tests(tmp_path, "tests/test_alpha.py") == ("tests/test_alpha.py",)


@pytest.mark.parametrize("path", ["tests/conftest.py", "pyproject.toml", "uv.lock"])
def test_config_changes_select_every_non_excluded_test(tmp_path, path):
    for test in (
        "tests/test_architecture.py",
        "tests/test_test_configuration.py",
        "tests/test_alpha.py",
        "tests/test_memory_postgres.py",
        "tests/test_web_access.py",
        "tests/test_web_accounts.py",
    ):
        _write(tmp_path, test)
    _write(tmp_path, "tests/helper.py")
    assert _tests(tmp_path, path) == (
        "tests/test_alpha.py",
        "tests/test_architecture.py",
        "tests/test_test_configuration.py",
    )


def test_alembic_config_selects_the_migration_tests(tmp_path):
    _write(tmp_path, "tests/test_migrations.py")
    _write(tmp_path, "tests/test_wordle_migrations.py")
    _write(tmp_path, "tests/test_unrelated.py")
    assert _tests(tmp_path, "alembic.ini") == ("tests/test_migrations.py",)


def test_imports_inside_functions_and_one_hop_are_selected(tmp_path):
    _write(tmp_path, "apps/api/app/services/thing.py")
    _write(tmp_path, "apps/api/app/services/helper.py", "from app.services.thing import run\n")
    _write(tmp_path, "apps/api/app/main.py", "from app.services.thing import run\n")
    _write(tmp_path, "tests/test_direct.py", "from app.services.thing import run\n")
    _write(tmp_path, "tests/test_package.py", "from app.services import thing\n")
    _write(tmp_path, "tests/test_nested.py", "def test_x():\n    import app.services.thing\n")
    _write(tmp_path, "tests/test_helper.py", "from app.services.helper import value\n")
    _write(tmp_path, "tests/test_main.py", "from app.main import app\n")
    assert _tests(tmp_path, "apps/api/app/services/thing.py") == (
        "tests/test_direct.py",
        "tests/test_helper.py",
        "tests/test_nested.py",
        "tests/test_package.py",
    )


def test_voice_agent_modules_are_top_level_and_use_file_stems(tmp_path):
    _write(tmp_path, "services/voice-agent/main.py")
    _write(tmp_path, "tests/test_voice.py", "from main import Agent\n")
    _write(tmp_path, "tests/test_delayed.py", "def test_x():\n    import main\n")
    _write(tmp_path, "tests/test_main_extra.py")
    assert _tests(tmp_path, "services/voice-agent/main.py") == (
        "tests/test_delayed.py",
        "tests/test_main_extra.py",
        "tests/test_voice.py",
    )


def test_scripts_match_text_names_and_stems_even_when_deleted(tmp_path):
    _write(tmp_path, "scripts/check_alpha.py")
    _write(tmp_path, "tests/test_path.py", "SCRIPT = 'scripts/check_alpha.py'\n")
    _write(tmp_path, "tests/test_name.py", "SCRIPT = 'check_alpha.py'\n")
    _write(tmp_path, "tests/test_check_alpha.py")
    _write(tmp_path, "tests/test_check_alpha_extra.py")
    _write(tmp_path, "tests/test_unrelated.py")
    expected = (
        "tests/test_check_alpha.py",
        "tests/test_check_alpha_extra.py",
        "tests/test_name.py",
        "tests/test_path.py",
    )
    assert _tests(tmp_path, "scripts/check_alpha.py") == expected
    (tmp_path / "scripts" / "check_alpha.py").unlink()
    assert _tests(tmp_path, "scripts/check_alpha.py") == expected


def test_migration_changes_select_migration_tests(tmp_path):
    _write(tmp_path, "tests/test_migrations.py")
    _write(tmp_path, "tests/test_wordle_migrations.py")
    _write(tmp_path, "tests/test_unrelated.py")
    assert _tests(tmp_path, "migrations/versions/abc123.py") == (
        "tests/test_migrations.py",
        "tests/test_wordle_migrations.py",
    )


@pytest.mark.parametrize("path", [".githooks/pre-push", "scripts/run_push_tests.sh"])
def test_hook_and_shell_changes_select_quality_tests(tmp_path, path):
    _write(tmp_path, "tests/test_quality_hooks.py")
    assert _tests(tmp_path, path) == ("tests/test_quality_hooks.py",)


def test_infra_changes_select_worker_packaging(tmp_path):
    _write(tmp_path, "tests/test_worker_packaging.py")
    assert _tests(tmp_path, "infra/app.yaml") == ("tests/test_worker_packaging.py",)


def test_contract_changes_select_contracts_and_mentions(tmp_path):
    _write(tmp_path, "tests/test_contracts.py")
    _write(tmp_path, "tests/test_voice_ui_commands.py", "openapi.json\n")
    _write(tmp_path, "tests/test_unrelated.py")
    assert _tests(tmp_path, "packages/contracts/openapi.json") == (
        "tests/test_contracts.py",
        "tests/test_voice_ui_commands.py",
    )


def test_external_service_tests_are_never_selected(tmp_path):
    _write(tmp_path, "tests/test_architecture.py")
    _write(tmp_path, "tests/test_test_configuration.py")
    _write(tmp_path, "tests/test_memory_postgres.py")
    _write(tmp_path, "tests/test_web_access.py")
    _write(tmp_path, "tests/test_web_accounts.py")
    expected = ("tests/test_architecture.py", "tests/test_test_configuration.py")
    assert _tests(tmp_path, "tests/test_memory_postgres.py") == expected
    assert _tests(tmp_path, "tests/test_web_access.py") == expected
    assert _tests(tmp_path, "tests/test_web_accounts.py") == expected


def test_docs_only_selects_nothing(tmp_path):
    assert _sel_to_tuple(_select(tmp_path, "docs/readme.md")) == ((), ())


def _sel_to_tuple(selection: select_tests.Selection) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return selection.tests, selection.web_files


def test_unparsable_tests_are_selected(tmp_path):
    _write(tmp_path, "apps/api/app/services/thing.py")
    _write(tmp_path, "tests/test_broken.py", "def (\n")
    assert _tests(tmp_path, "apps/api/app/services/thing.py") == ("tests/test_broken.py",)


def test_missing_named_test_files_are_skipped(tmp_path):
    assert _tests(tmp_path, "apps/api/app/services/stem_only.py") == ()
    assert _tests(tmp_path, "packages/contracts/openapi.json") == ()


def test_deleted_modules_still_match_tests_that_import_them(tmp_path):
    _write(tmp_path, "tests/test_gone.py", "from app.services.gone import value\n")
    assert _tests(tmp_path, "apps/api/app/services/gone.py") == ("tests/test_gone.py",)


def test_importers_are_followed_transitively_through_module_trees(tmp_path):
    _write(tmp_path, "apps/api/app/services/base.py")
    _write(tmp_path, "apps/api/app/services/middle.py", "from app.services.base import run\n")
    _write(tmp_path, "apps/api/app/services/top.py", "from app.services.middle import run\n")
    _write(tmp_path, "tests/test_top.py", "from app.services.top import run\n")
    _write(tmp_path, "tests/test_middle.py", "from app.services.middle import run\n")
    assert _tests(tmp_path, "apps/api/app/services/base.py") == (
        "tests/test_middle.py",
        "tests/test_top.py",
    )


def test_route_needles_come_from_route_modules_in_the_importer_closure(tmp_path):
    _write(tmp_path, "apps/api/app/services/base.py")
    _write(tmp_path, "apps/api/app/services/middle.py", "from app.services.base import run\n")
    _write(tmp_path, "apps/api/app/routes/thing.py", "from app.services.middle import run\nROUTE = '/v1/things'\n")
    _write(tmp_path, "tests/test_route.py", "from app.main import app\nROUTE = '/v1/things'\n")
    _write(tmp_path, "tests/test_unrelated.py", "from app.main import app\nROUTE = '/v1/other'\n")
    assert _tests(tmp_path, "apps/api/app/services/base.py") == ("tests/test_route.py",)


def test_tests_importing_selected_tests_are_selected_transitively(tmp_path):
    _write(tmp_path, "apps/api/app/services/base.py")
    _write(tmp_path, "tests/test_base.py", "from app.services.base import run\n")
    _write(tmp_path, "tests/test_follow.py", "from test_base import run\n")
    assert _tests(tmp_path, "apps/api/app/services/base.py") == (
        "tests/test_base.py",
        "tests/test_follow.py",
    )


def test_relative_imports_reach_the_changed_module(tmp_path):
    _write(tmp_path, "apps/api/app/services/base.py")
    _write(tmp_path, "apps/api/app/services/middle.py", "from . import base\n")
    _write(tmp_path, "tests/test_middle.py", "from app.services.middle import base\n")
    assert _tests(tmp_path, "apps/api/app/services/base.py") == ("tests/test_middle.py",)


def test_import_of_nested_packages_selects_changed_init(tmp_path):
    _write(tmp_path, "apps/api/app/services/__init__.py")
    _write(tmp_path, "tests/test_package.py", "import app.services.wordle\n")
    assert _tests(tmp_path, "apps/api/app/services/__init__.py") == ("tests/test_package.py",)


def test_main_is_not_expanded_through_so_its_tests_need_a_direct_change(tmp_path):
    _write(tmp_path, "apps/api/app/services/base.py")
    _write(tmp_path, "apps/api/app/main.py", "from app.services.base import run\n")
    _write(tmp_path, "tests/test_main.py", "from app.main import app\n")
    _write(tmp_path, "tests/test_base.py", "from app.services.base import run\n")
    assert _tests(tmp_path, "apps/api/app/services/base.py") == ("tests/test_base.py",)
    assert _tests(tmp_path, "apps/api/app/main.py") == ("tests/test_main.py",)


def test_orphan_module_falls_back_to_main_importers_and_route_mentions(tmp_path):
    _write(tmp_path, "apps/api/app/services/orphan.py", "ROUTE = '/internal/agent/tool/orphan'\n")
    _write(tmp_path, "tests/test_route.py", "from app.main import create_app\nROUTE = '/internal/agent/tool/orphan'\n")
    _write(tmp_path, "tests/test_unrelated.py", "VALUE = '/internal/agent/tool/orphan'\n")
    assert _tests(tmp_path, "apps/api/app/services/orphan.py") == ("tests/test_route.py",)


def test_empty_module_selection_uses_the_final_fallback(tmp_path):
    for path in (
        "tests/test_contracts.py",
        "tests/test_finalized_commands.py",
        "tests/test_worker_alpha.py",
        "tests/test_other.py",
    ):
        _write(tmp_path, path)
    _write(tmp_path, "apps/api/app/services/orphan.py")
    assert _tests(tmp_path, "apps/api/app/services/orphan.py") == (
        "tests/test_contracts.py",
        "tests/test_finalized_commands.py",
        "tests/test_worker_alpha.py",
    )


def test_real_wordle_tools_selects_the_wordle_tool_route_tests():
    selected = _tests(ROOT, "apps/api/app/services/wordle_tools.py")
    assert "tests/test_wordle_tool_routes.py" in selected


def test_real_authorization_selects_memory_route_tests():
    selected = _tests(ROOT, "apps/api/app/services/authorization.py")
    assert "tests/test_memory_routes.py" in selected


def test_changed_test_helpers_select_their_importers_transitively(tmp_path):
    _write(tmp_path, "tests/helper.py")
    _write(tmp_path, "tests/sub/helper2.py")
    _write(tmp_path, "tests/test_middle.py", "from sub.helper2 import value\n")
    _write(tmp_path, "tests/test_top.py", "from test_middle import value\n")
    _write(tmp_path, "tests/test_other.py")
    assert _tests(tmp_path, "tests/sub/helper2.py") == ("tests/test_middle.py", "tests/test_top.py")
    assert _tests(tmp_path, "tests/helper.py") == ()


def test_non_python_test_data_selects_mentioning_tests_or_the_always_tests(tmp_path):
    _write(tmp_path, "tests/test_data.py", "DATA = 'tests/fixtures/cases.json'\n")
    _write(tmp_path, "tests/test_names.py", "NAME = 'cases.json'\n")
    _write(tmp_path, "tests/test_architecture.py")
    _write(tmp_path, "tests/test_test_configuration.py")
    assert _tests(tmp_path, "tests/fixtures/cases.json") == (
        "tests/test_architecture.py",
        "tests/test_data.py",
        "tests/test_names.py",
        "tests/test_test_configuration.py",
    )
    _write(tmp_path, "tests/fixtures/unused.bin", "\x00")
    assert _tests(tmp_path, "tests/fixtures/unused.bin") == (
        "tests/test_architecture.py",
        "tests/test_test_configuration.py",
    )


def test_non_utf8_and_unparsable_files_never_crash(tmp_path):
    _write(tmp_path, "apps/api/app/services/base.py")
    _write_bytes(tmp_path, "tests/test_broken.py", b"\xff\xfe")
    _write_bytes(tmp_path, "apps/api/app/services/bad.py", b"\xff\xfe")
    _write(tmp_path, "tests/test_bad_module.py", "from app.services.bad import value\n")
    assert "tests/test_broken.py" in _tests(tmp_path, "apps/api/app/services/base.py")
    assert "tests/test_bad_module.py" in _tests(tmp_path, "apps/api/app/services/bad.py")


def test_non_utf8_selected_paths_are_printed_as_bytes(tmp_path, capsysbinary):
    name = os.fsdecode(b"test_\xff.py")
    target = tmp_path / "tests" / name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")
    assert select_tests.main(["--root", str(tmp_path), f"tests/{name}"]) == 0
    assert capsysbinary.readouterr().out == os.fsencode(f"tests/{name}") + b"\n"


def test_web_sources_are_reported_separately(tmp_path):
    _write(tmp_path, "apps/web/src/lib/api.ts")
    _write(tmp_path, "apps/web/src/routes/+page.svelte")
    selection = _select(tmp_path, "apps/web/src/lib/api.ts", "apps/web/src/routes/+page.svelte", "README.md")
    assert selection.tests == ()
    assert selection.web_files == ("apps/web/src/lib/api.ts", "apps/web/src/routes/+page.svelte")


def test_real_voice_agent_main_selects_fallback_and_budget():
    selected = _tests(ROOT, "services/voice-agent/main.py")
    assert "tests/test_fallback.py" in selected
    assert "tests/test_worker_budget.py" in selected


def test_real_worker_route_selects_worker_tests():
    selected = _tests(ROOT, "apps/api/app/routes/worker.py")
    assert any(Path(path).name.startswith("test_worker_") for path in selected)


def test_real_docs_path_selects_nothing():
    assert _tests(ROOT, "docs/execution_checklist_v5.md") == ()


def test_real_selection_never_contains_external_service_tests():
    selected = _tests(
        ROOT,
        "apps/api/app/routes/worker.py",
        "apps/api/app/domain/contracts.py",
        "services/voice-agent/main.py",
        "scripts/check_openai.py",
        "tests/test_memory_postgres.py",
        "tests/test_web_access.py",
        "tests/test_web_accounts.py",
    )
    assert not [
        path
        for path in selected
        if path.endswith("_postgres.py") or path in ("tests/test_web_access.py", "tests/test_web_accounts.py")
    ]


MODULE_PATHS = tuple(
    path.relative_to(ROOT).as_posix()
    for tree in ("apps/api/app", "services/voice-agent", "scripts")
    for path in sorted((ROOT / tree).rglob("*.py"))
    if path.name != "__init__.py"
)


@pytest.mark.parametrize("path", MODULE_PATHS)
def test_every_real_module_selects_relevant_tests(path):
    selected = _tests(ROOT, path)
    assert set(selected) - set(select_tests.ALWAYS_TESTS), path
    assert not [test for test in selected if select_tests._is_excluded(test)]
