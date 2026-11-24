"""Exercise the installed Git hooks without using a developer's Git configuration."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("install_hooks.sh", "check_quality.sh", "check_types.sh", "run_push_tests.sh", "select_tests.py")
ZERO_SHA = "0" * 40


def _copy_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "config", "--local", "user.email", "hook@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "--local", "user.name", "Hook Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "--local", "commit.gpgsign", "false"], cwd=repo, check=True)
    shutil.copytree(ROOT / ".githooks", repo / ".githooks")
    (repo / "scripts").mkdir()
    for script in SCRIPTS:
        shutil.copy(ROOT / "scripts" / script, repo / "scripts" / script)
    _write(repo / ".gitignore", "apps/web/node_modules/\nartifacts/\n")
    return repo


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _push_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = _copy_repo(tmp_path)
    _write(repo / "tests" / "test_sample.py", "def test_sample():\n    assert True\n")
    first = _commit(repo, "initial")
    _write(repo / "tests" / "test_sample.py", "def test_sample():\n    assert 1 == 1\n")
    second = _commit(repo, "change test")
    _git(repo, "update-ref", "refs/remotes/origin/main", first)
    return repo, first, second


def _fake_uv(tmp_path: Path) -> Path:
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text(
        """#!/bin/sh
log="${FAKE_UV_LOG:-/dev/null}"
printf '%s\\n' "$*" >>"$log"
if [ "${1:-}" = "run" ]; then
  shift
  [ "${1:-}" = "--locked" ] && shift
  case "${1:-}" in
    ruff) exit "${FAKE_RUFF_STATUS:-0}" ;;
    mypy) exit "${FAKE_MYPY_STATUS:-0}" ;;
    pytest)
      printf 'GIT_DIR=%s\\n' "${GIT_DIR-<unset>}" >>"$log"
      printf 'PYTEST_ADDOPTS=%s\\n' "${PYTEST_ADDOPTS-<unset>}" >>"$log"
      printf 'PYTEST_PLUGINS=%s\\n' "${PYTEST_PLUGINS-<unset>}" >>"$log"
      printf 'PYTHONPATH=%s\\n' "${PYTHONPATH-<unset>}" >>"$log"
      printf 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=%s\\n' "${PYTEST_DISABLE_PLUGIN_AUTOLOAD-<unset>}" >>"$log"
      printf 'PYTHONOPTIMIZE=%s\\n' "${PYTHONOPTIMIZE-<unset>}" >>"$log"
      exit "${FAKE_PYTEST_STATUS:-0}"
      ;;
    python)
      shift
      exec python3 "$@"
      ;;
  esac
fi
exit 0
"""
    )
    uv.chmod(0o755)
    return binary


def _fake_pnpm(binary: Path) -> Path:
    pnpm = binary / "pnpm"
    pnpm.write_text(
        """#!/bin/sh
log="${FAKE_PNPM_LOG:-/dev/null}"
printf '%s\\n' "$*" >>"$log"
case "$*" in
  *"--reporter=json"*)
    if [ "${FAKE_PNPM_RELATED:-present}" = "empty" ]; then
      printf '%s\\n' '{"testResults": []}'
    else
      printf '%s\\n' '{"testResults": [{"name": "/repo/apps/web/src/lib/api.test.ts"}]}'
    fi
    ;;
esac
exit "${FAKE_PNPM_STATUS:-0}"
"""
    )
    pnpm.chmod(0o755)
    return pnpm


def _environment(binary: Path, **values: str) -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment["PATH"] = f"{binary}:{environment['PATH']}"
    environment.update(values)
    return environment


def _run_pre_push(repo: Path, environment: dict[str, str], local_sha: str, remote_sha: str = ZERO_SHA):
    stdin = f"refs/heads/feature {local_sha} refs/heads/feature {remote_sha}\n"
    return subprocess.run(
        ["bash", ".githooks/pre-push"],
        cwd=repo,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push"])
def test_installed_hooks_reject_failed_quality_check(tmp_path, hook):
    repo = _copy_repo(tmp_path)
    subprocess.run(["bash", "scripts/install_hooks.sh"], cwd=repo, check=True)
    assert _git(repo, "config", "--local", "core.hooksPath") == ".githooks"
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text("#!/bin/sh\nexit 23\n")
    uv.chmod(0o755)
    environment = os.environ | {"PATH": f"{binary}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", f".githooks/{hook}"], cwd=repo, env=environment, check=False)
    assert result.returncode == 23


def test_pre_push_blocks_failing_pytest_with_its_status(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log), FAKE_PYTEST_STATUS="17")
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 17
    assert "tests/test_sample.py" in result.stdout


def test_pre_push_deletion_runs_no_tests(tmp_path):
    repo, _, _ = _push_repo(tmp_path)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log), FAKE_PYTEST_STATUS="19")
    result = _run_pre_push(repo, environment, ZERO_SHA)
    assert result.returncode == 0
    assert "pytest" not in log.read_text()


def test_pre_push_refuses_a_dirty_tree(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    _write(repo / "tests" / "test_sample.py", "def test_sample():\n    assert False\n")
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 1
    assert "modified or staged" in result.stderr
    assert "pytest" not in log.read_text()


def test_pre_push_refuses_a_sha_other_than_head(tmp_path):
    repo, first, _ = _push_repo(tmp_path)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log))
    result = _run_pre_push(repo, environment, first)
    assert result.returncode == 1
    assert "only HEAD" in result.stderr
    assert "pytest" not in log.read_text()


def test_pre_push_hides_git_environment_from_tests(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log), GIT_DIR=str(repo / ".git"))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 0
    assert "GIT_DIR=<unset>" in log.read_text()


def test_pre_push_quality_failure_stops_before_tests(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log), FAKE_RUFF_STATUS="23")
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 23
    assert "pytest" not in log.read_text()


def _web_repo(tmp_path: Path, path: str = "apps/web/src/lib/api.ts", *, delete: bool = False) -> tuple[Path, str]:
    repo = _copy_repo(tmp_path)
    _write(repo / path, "export const value = 1;\n")
    first = _commit(repo, "initial web source")
    if delete:
        (repo / path).unlink()
    else:
        _write(repo / path, "export const value = 2;\n")
    second = _commit(repo, "change web source")
    _git(repo, "update-ref", "refs/remotes/origin/main", first)
    return repo, second


def test_pre_push_refuses_untracked_source_files(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    _write(repo / "tests" / "test_untracked.py", "def test_x():\n    assert True\n")
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 1
    assert "untracked files" in result.stderr
    assert "tests/test_untracked.py" in result.stderr


def test_pre_push_allows_untracked_files_outside_source_trees(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    _write(repo / "NOTES.txt", "scratch\n")
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 0


def test_pre_push_reports_deleted_tests_without_failing(tmp_path):
    repo = _copy_repo(tmp_path)
    _write(repo / "tests" / "test_gone.py", "def test_gone():\n    assert True\n")
    first = _commit(repo, "initial test")
    (repo / "tests" / "test_gone.py").unlink()
    second = _commit(repo, "delete test")
    _git(repo, "update-ref", "refs/remotes/origin/main", first)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 0
    assert "Deleted test file: tests/test_gone.py" in result.stderr
    assert not any(line.startswith("pytest") for line in log.read_text().splitlines())


def test_pre_push_accepts_a_tag_that_resolves_to_head(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    subprocess.run(["git", "tag", "-a", "v1", "-m", "tag", second], cwd=repo, check=True)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log))
    result = _run_pre_push(repo, environment, "v1")
    assert result.returncode == 0
    assert "Selected 1 test file(s)." in result.stdout


def test_pre_push_clears_pytest_environment(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    log = tmp_path / "uv.log"
    environment = _environment(
        _fake_uv(tmp_path),
        FAKE_UV_LOG=str(log),
        PYTEST_ADDOPTS="-x",
        PYTEST_PLUGINS="plugin",
        PYTHONPATH="/tmp/example",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONOPTIMIZE="2",
    )
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 0
    output = log.read_text()
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONOPTIMIZE"):
        assert f"{name}=<unset>" in output


def test_pre_push_blocks_exit_status_five(tmp_path):
    repo, _, second = _push_repo(tmp_path)
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log), FAKE_PYTEST_STATUS="5")
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 5


def test_pre_push_reports_base_resolution_failure(tmp_path):
    repo = _copy_repo(tmp_path)
    _write(repo / "tests" / "test_sample.py", "def test_sample():\n    assert True\n")
    head = _commit(repo, "initial")
    log = tmp_path / "uv.log"
    environment = _environment(_fake_uv(tmp_path), FAKE_UV_LOG=str(log))
    result = _run_pre_push(repo, environment, head, "f" * 40)
    assert result.returncode == 1
    assert "Cannot determine a base commit" in result.stderr


def test_pre_push_refuses_web_changes_without_node_modules(tmp_path):
    repo, second = _web_repo(tmp_path)
    binary = _fake_uv(tmp_path)
    _fake_pnpm(binary)
    log = tmp_path / "pnpm.log"
    environment = _environment(binary, FAKE_UV_LOG=str(tmp_path / "uv.log"), FAKE_PNPM_LOG=str(log))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 1
    assert "node_modules is missing" in result.stderr
    assert not log.exists()


def test_pre_push_runs_the_full_unit_suite_for_web_configuration(tmp_path):
    repo, _ = _web_repo(tmp_path, "apps/web/package.json")
    (repo / "apps" / "web" / "node_modules").mkdir()
    binary = _fake_uv(tmp_path)
    _fake_pnpm(binary)
    log = tmp_path / "pnpm.log"
    environment = _environment(binary, FAKE_UV_LOG=str(tmp_path / "uv.log"), FAKE_PNPM_LOG=str(log))
    result = _run_pre_push(repo, environment, _git(repo, "rev-parse", "HEAD"))
    assert result.returncode == 0
    assert "--dir apps/web test:unit" in log.read_text()


def test_pre_push_runs_related_vitest_files_without_pass_with_no_tests(tmp_path):
    repo, second = _web_repo(tmp_path)
    (repo / "apps" / "web" / "node_modules").mkdir()
    binary = _fake_uv(tmp_path)
    _fake_pnpm(binary)
    log = tmp_path / "pnpm.log"
    environment = _environment(binary, FAKE_UV_LOG=str(tmp_path / "uv.log"), FAKE_PNPM_LOG=str(log))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 0
    assert "Related Vitest files:" in result.stdout
    lines = log.read_text().splitlines()
    assert any("--passWithNoTests --reporter=json -- src/lib/api.ts" in line for line in lines)
    assert any("vitest related --run -- src/lib/api.ts" in line and "--passWithNoTests" not in line for line in lines)


def test_pre_push_treats_no_related_vitest_files_as_success(tmp_path):
    repo, second = _web_repo(tmp_path)
    (repo / "apps" / "web" / "node_modules").mkdir()
    binary = _fake_uv(tmp_path)
    _fake_pnpm(binary)
    log = tmp_path / "pnpm.log"
    environment = _environment(
        binary,
        FAKE_UV_LOG=str(tmp_path / "uv.log"),
        FAKE_PNPM_LOG=str(log),
        FAKE_PNPM_RELATED="empty",
    )
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 0
    assert "No related Vitest files." in result.stdout
    assert [line for line in log.read_text().splitlines() if "vitest related" in line] != []


def test_pre_push_does_not_run_vitest_for_deleted_web_files(tmp_path):
    repo, second = _web_repo(tmp_path, delete=True)
    binary = _fake_uv(tmp_path)
    _fake_pnpm(binary)
    log = tmp_path / "pnpm.log"
    environment = _environment(binary, FAKE_UV_LOG=str(tmp_path / "uv.log"), FAKE_PNPM_LOG=str(log))
    result = _run_pre_push(repo, environment, second)
    assert result.returncode == 0
    assert "node_modules is missing" not in result.stderr
    assert not log.exists()


def test_installer_preserves_existing_hooks(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    existing = tmp_path / ".git" / "hooks" / "pre-commit"
    existing.write_text("existing hook\n")
    result = subprocess.run(["bash", str(ROOT / "scripts/install_hooks.sh")], cwd=tmp_path, check=False)
    assert result.returncode == 1
    assert existing.read_text() == "existing hook\n"
    assert (
        subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=tmp_path, check=False).returncode
        == 1
    )


def test_mypy_warning_with_zero_status_is_rejected(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text("#!/bin/sh\necho 'Warning: unused configuration'\nexit 0\n")
    uv.chmod(0o755)
    environment = os.environ | {"PATH": f"{binary}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(ROOT / "scripts/check_types.sh")], cwd=tmp_path, env=environment, check=False)
    assert result.returncode == 1
