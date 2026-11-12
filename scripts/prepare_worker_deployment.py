"""Prepare an isolated, source-only LiveKit build context."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "artifacts/deployment/worker"


def prepare():
    DEST.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, DEST / name)
    shutil.copy2(ROOT / "infra/agent.Dockerfile", DEST / "Dockerfile")
    target = DEST / "services/voice-agent"
    target.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "services/voice-agent").glob("*.py"):
        shutil.copy2(source, target / source.name)
    (DEST / ".dockerignore").write_text(".env\n.env.*\n.git\n.venv\n__pycache__\n*.db\n*.log\n")
    print(DEST.relative_to(ROOT))


if __name__ == "__main__":
    prepare()
