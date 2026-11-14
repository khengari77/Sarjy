"""Prepare an isolated, source-only LiveKit build context."""

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "artifacts/deployment/worker"
# The worker loads the same authoritative Wordle allowlist as the web app.
CONTRACT_FILES = (
    "packages/contracts/wordle_commands.json",
    "packages/contracts/wordle_command_fixtures.json",
)


def _copy_file(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise ValueError("BUILD_SOURCE_SYMLINK")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _populate(root: Path, staging: Path) -> None:
    for name in ("pyproject.toml", "uv.lock"):
        _copy_file(root / name, staging / name)
    _copy_file(root / "infra/agent.Dockerfile", staging / "Dockerfile")
    for directory in ("services/voice-agent", "apps/api/app/domain"):
        sources = tuple(
            source
            for source in (root / directory).rglob("*.py")
            if not any(part.startswith(".") or part == "__pycache__" for part in source.relative_to(root).parts)
        )
        if not sources:
            raise ValueError(f"BUILD_SOURCE_MISSING: {directory}")
        for source in sources:
            if any(parent.is_symlink() for parent in source.parents if parent != root and root in parent.parents):
                raise ValueError("BUILD_SOURCE_SYMLINK")
            _copy_file(source, staging / source.relative_to(root))
    for name in CONTRACT_FILES:
        _copy_file(root / name, staging / name)
    (staging / ".dockerignore").write_text(".env\n.env.*\n.git\n.venv\n__pycache__\n*.db\n*.log\n")


def prepare() -> None:
    if DEST.is_symlink():
        raise ValueError("BUILD_DESTINATION_SYMLINK")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="worker-staging-", dir=DEST.parent) as temporary:
        staging = Path(temporary) / "context"
        staging.mkdir()
        _populate(ROOT, staging)
        # Only discard the owned output after every required source was copied.
        if DEST.exists():
            shutil.rmtree(DEST)
        shutil.move(str(staging), DEST)
    print(DEST.relative_to(ROOT))


if __name__ == "__main__":
    prepare()
