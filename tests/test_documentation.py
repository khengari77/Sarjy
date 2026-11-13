"""Keep documentation linked and truthful as the implementation moves."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "node_modules", "artifacts", ".svelte-kit", "build", "test-results", "__pycache__"}
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FORBIDDEN = (
    "not present on this branch",
    "not-yet-merged",
    "Until that service is merged",
    "are Day 2 work",
)


def _files(suffix: str):
    for path in ROOT.rglob(f"*{suffix}"):
        if not EXCLUDED.intersection(path.parts):
            yield path


def test_local_markdown_links_resolve():
    broken = []
    for path in _files(".md"):
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0].strip().strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = ROOT / target.lstrip("/") if target.startswith("/") else path.parent / target
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "\n".join(broken)


def test_retired_status_phrases_are_gone():
    stale = []
    for path in [*_files(".md"), *_files(".py")]:
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN:
            if phrase in text:
                stale.append(f"{path.relative_to(ROOT)}: {phrase}")
    assert not stale, "\n".join(stale)
