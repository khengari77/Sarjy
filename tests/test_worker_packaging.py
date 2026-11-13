"""Build contexts contain runtime source, never stale files or local secrets."""

import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_worker_deployment.py"


@pytest.fixture
def packaging(tmp_path):
    root = tmp_path / "repository"
    files = {
        "pyproject.toml": "[project]\nname = 'fixture'\n",
        "uv.lock": "fixture lock",
        "infra/agent.Dockerfile": "COPY apps/api/app/domain ./shared/app/domain\n",
        "services/voice-agent/main.py": "import app.domain.contracts\n",
        "apps/api/app/domain/contracts.py": "VALUE = 1\n",
        "apps/api/app/domain/nested/module.py": "VALUE = 2\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    prepare = runpy.run_path(str(SCRIPT))["prepare"]
    destination = root / "artifacts/deployment/worker"
    prepare.__globals__.update(ROOT=root, DEST=destination)
    return root, destination, prepare, files


def test_context_contains_exact_runtime_sources_and_replaces_stale_output(packaging, capsys):
    root, destination, prepare, files = packaging
    destination.mkdir(parents=True)
    (destination / "old-secret.env").write_text("stale")
    for name in (
        ".env",
        "recording.wav",
        "sarjy.db",
        "services/voice-agent/local.env",
        "apps/api/app/domain/__pycache__/cached.py",
        "apps/api/app/domain/.cache/hidden.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not source")
    prepare()
    expected = set(files) - {"infra/agent.Dockerfile"} | {"Dockerfile", ".dockerignore"}
    assert {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()} == expected
    for name in set(files) - {"infra/agent.Dockerfile"}:
        assert (destination / name).read_bytes() == (root / name).read_bytes()
    assert (destination / "Dockerfile").read_bytes() == (root / "infra/agent.Dockerfile").read_bytes()
    assert capsys.readouterr().out.strip() == "artifacts/deployment/worker"
    prepare()
    assert not list(destination.parent.glob("worker-staging-*"))


@pytest.mark.parametrize("missing", ["uv.lock", "apps/api/app/domain/contracts.py"])
def test_failed_preparation_preserves_previous_output(packaging, missing):
    root, destination, prepare, _ = packaging
    destination.mkdir(parents=True)
    (destination / "previous").write_text("keep")
    if missing.endswith("contracts.py"):
        import shutil

        shutil.rmtree(root / "apps/api/app/domain")
    else:
        (root / missing).unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        prepare()
    assert (destination / "previous").read_text() == "keep"
    assert not list(destination.parent.glob("worker-staging-*"))


def test_symlinked_source_is_rejected(packaging, tmp_path):
    root, destination, prepare, _ = packaging
    secret = tmp_path / "private"
    secret.write_text("not for packaging")
    (root / "services/voice-agent/local.py").symlink_to(secret)
    with pytest.raises(ValueError, match="BUILD_SOURCE_SYMLINK"):
        prepare()
    assert not destination.exists()


def test_symlinked_destination_is_not_modified(packaging, tmp_path):
    _, destination, prepare, _ = packaging
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep").write_text("keep")
    destination.parent.mkdir(parents=True)
    destination.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="BUILD_DESTINATION_SYMLINK"):
        prepare()
    assert (other / "keep").read_text() == "keep"
