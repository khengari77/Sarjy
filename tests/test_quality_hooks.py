"""Exercise the installed Git hooks without using a developer's Git configuration."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push"])
def test_installed_hooks_reject_failed_quality_check(tmp_path, hook):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shutil.copytree(ROOT / ".githooks", repo / ".githooks")
    (repo / "scripts").mkdir()
    for script in ("install_hooks.sh", "check_quality.sh"):
        shutil.copy(ROOT / "scripts" / script, repo / "scripts" / script)
    subprocess.run(["bash", "scripts/install_hooks.sh"], cwd=repo, check=True)
    assert (
        subprocess.check_output(["git", "config", "--local", "core.hooksPath"], cwd=repo, text=True).strip()
        == ".githooks"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text("#!/bin/sh\nexit 23\n")
    uv.chmod(0o755)
    environment = os.environ | {"PATH": f"{binary}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", f".githooks/{hook}"], cwd=repo, env=environment, check=False)
    assert result.returncode == 23


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
