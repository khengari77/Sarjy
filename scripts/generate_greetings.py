"""Pre-render the English greeting audio for every catalog voice.

The browser plays these clips at call start; the worker does not use them.  The
default run is an offline dry run that prints the voices, files and exact
character count.  ``--live`` sends those texts to Deepgram TTS; the owner
approved this one-time development generation separately from the production
daily character ledger, so this script does not reserve against that ledger.

Outputs ``apps/web/static/greetings/<voice_id>/<kind>.mp3`` and a stable
``apps/web/static/greetings/manifest.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, TextIO

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.domain.deepgram_voices import DEEPGRAM_VOICES  # noqa: E402
from app.domain.intro import INTRO, SHORT_GREETING  # noqa: E402

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"
ENCODING = "mp3"
BIT_RATE = "48000"
MANIFEST_VERSION = 2
MANIFEST_NAME = "manifest.json"
GREETINGS_DIR = ROOT / "apps/web/static/greetings"
KINDS = ("full", "short")
TEXTS: dict[str, str] = {"full": INTRO["en"], "short": SHORT_GREETING["en"]}
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT_S = 30.0
DEFAULT_RETRY_WAIT_S = 1.0
CATALOG_VOICE_IDS = frozenset(voice.id for voice in DEEPGRAM_VOICES)


@dataclass(frozen=True)
class PlannedClip:
    voice_id: str
    kind: str
    text: str
    target: Path
    skip: bool


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_sha256(text: str) -> str:
    return _sha256(text.encode("utf-8"))


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _voice_entries(manifest: dict[str, Any]) -> dict[str, Any]:
    voices = manifest.get("voices")
    return voices if isinstance(voices, dict) else {}


def _recorded_entry(manifest: dict[str, Any], voice_id: str, kind: str) -> dict[str, Any] | None:
    recorded = _voice_entries(manifest).get(voice_id)
    if not isinstance(recorded, dict):
        return None
    entry = recorded.get(kind)
    return entry if isinstance(entry, dict) else None


def _file_matches(path: Path, recorded: dict[str, Any]) -> bool:
    if not path.is_file():
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return recorded.get("bytes") == len(data) and recorded.get("sha256") == _sha256(data)


def _settings_match(recorded: dict[str, Any], text_sha256: str) -> bool:
    return (
        recorded.get("text_sha256") == text_sha256
        and recorded.get("encoding") == ENCODING
        and recorded.get("bit_rate") == BIT_RATE
    )


def _plan(voice_ids: Sequence[str], *, force: bool, greetings_dir: Path, manifest: dict[str, Any]) -> list[PlannedClip]:
    clips: list[PlannedClip] = []
    for voice_id in voice_ids:
        for kind in KINDS:
            text = TEXTS[kind]
            target = greetings_dir / voice_id / f"{kind}.mp3"
            recorded = _recorded_entry(manifest, voice_id, kind)
            unchanged = recorded is not None and _settings_match(recorded, _text_sha256(text))
            skip = not force and unchanged and _file_matches(target, recorded)
            clips.append(PlannedClip(voice_id=voice_id, kind=kind, text=text, target=target, skip=skip))
    return clips


def _retry_after_seconds(response: httpx.Response) -> float:
    header = response.headers.get("retry-after")
    if not header:
        return DEFAULT_RETRY_WAIT_S
    try:
        seconds = float(header)
    except ValueError:
        try:
            when = parsedate_to_datetime(header)
        except (TypeError, ValueError):
            return DEFAULT_RETRY_WAIT_S
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_WAIT_S)


def _post_with_retries(
    client: httpx.Client, *, params: dict[str, str], headers: dict[str, str], payload: dict[str, str]
) -> httpx.Response:
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.post(DEEPGRAM_SPEAK_URL, params=params, headers=headers, json=payload)
        except httpx.TimeoutException:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            continue
        if response.status_code == 429:
            if attempt == MAX_ATTEMPTS - 1:
                response.raise_for_status()
            time.sleep(_retry_after_seconds(response))
            continue
        if response.status_code >= 500:
            if attempt == MAX_ATTEMPTS - 1:
                response.raise_for_status()
            continue
        response.raise_for_status()
        return response
    raise AssertionError("retry loop exited without a response")


def _synthesize(client: httpx.Client, *, voice_id: str, text: str, api_key: str) -> bytes:
    params = {"model": voice_id, "encoding": ENCODING, "bit_rate": BIT_RATE}
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
    response = _post_with_retries(client, params=params, headers=headers, payload={"text": text})
    if not response.content:
        raise httpx.HTTPError("Deepgram returned an empty body")
    return response.content


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _describe_error(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _current_texts() -> dict[str, dict[str, Any]]:
    return {kind: {"sha256": _text_sha256(text), "chars": len(text)} for kind, text in TEXTS.items()}


def _entry_for(path: Path, text: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "bytes": len(data),
        "sha256": _sha256(data),
        "text_sha256": _text_sha256(text),
        "encoding": ENCODING,
        "bit_rate": BIT_RATE,
    }


def _print_plan(clips: Sequence[PlannedClip], pending: Sequence[PlannedClip], *, live: bool, out: TextIO) -> None:
    mode = "live" if live else "dry run"
    print(f"Greeting audio plan ({mode})", file=out)
    print(f"Voices: {len({clip.voice_id for clip in clips})}", file=out)
    for clip in clips:
        status = "skip" if clip.skip else "generate"
        print(f"  {clip.voice_id}/{clip.kind}.mp3  {len(clip.text)} chars  [{status}]", file=out)
    print(f"Files to write: {len(pending)}", file=out)
    print(f"Total characters: {sum(len(clip.text) for clip in pending)}", file=out)


def _selected_voice_ids(voices: Sequence[str] | None) -> list[str]:
    if voices is None:
        return [voice.id for voice in DEEPGRAM_VOICES]
    return list(dict.fromkeys(voices))


def _validate_voice_ids(voice_ids: Sequence[str]) -> None:
    unknown = [voice_id for voice_id in voice_ids if voice_id not in CATALOG_VOICE_IDS]
    if unknown:
        raise ValueError(f"unknown voice id(s): {', '.join(unknown)}")


def generate(
    *,
    live: bool,
    force: bool = False,
    voices: Sequence[str] | None = None,
    greetings_dir: Path = GREETINGS_DIR,
    client: httpx.Client | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    voice_ids = _selected_voice_ids(voices)
    _validate_voice_ids(voice_ids)
    manifest_path = greetings_dir / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    clips = _plan(voice_ids, force=force, greetings_dir=greetings_dir, manifest=manifest)
    pending = [clip for clip in clips if not clip.skip]
    _print_plan(clips, pending, live=live, out=stdout)
    if not live:
        print("Dry run only; pass --live to call Deepgram.", file=stdout)
        return 0

    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        print("DEEPGRAM_API_KEY is not set", file=stderr)
        return 2
    print("Development generation; the production TTS character ledger is not charged.", file=stdout)

    manifest["version"] = MANIFEST_VERSION
    manifest["encoding"] = ENCODING
    manifest["texts"] = _current_texts()
    manifest["voices"] = _voice_entries(manifest)
    failed: list[str] = []
    owned_client = client is None
    active_client = client if client is not None else httpx.Client(timeout=60.0)
    try:
        for voice_id in voice_ids:
            for clip in (clip for clip in clips if clip.voice_id == voice_id):
                if clip.skip:
                    continue
                try:
                    audio = _synthesize(active_client, voice_id=voice_id, text=clip.text, api_key=api_key)
                except httpx.HTTPError as exc:
                    print(f"error: {voice_id}/{clip.kind}: {_describe_error(exc)}", file=stderr)
                    if voice_id not in failed:
                        failed.append(voice_id)
                    continue
                _write_bytes(clip.target, audio)
                voice_entry = manifest["voices"].get(voice_id)
                if not isinstance(voice_entry, dict):
                    voice_entry = {}
                    manifest["voices"][voice_id] = voice_entry
                voice_entry[clip.kind] = _entry_for(clip.target, clip.text)
                _write_manifest(manifest_path, manifest)
                print(f"wrote {_display_path(clip.target)} ({len(audio)} bytes)", file=stdout)
    finally:
        if owned_client:
            active_client.close()

    if pending:
        print(f"Manifest: {_display_path(manifest_path)}", file=stdout)
    if failed:
        print(f"Failed voices: {', '.join(failed)}", file=stderr)
        return 1
    return 0


def upgrade_manifest(
    greetings_dir: Path = GREETINGS_DIR, *, out: TextIO | None = None, err: TextIO | None = None
) -> int:
    """Add per-clip text/settings fields to an existing version-1 manifest, offline."""
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    manifest_path = greetings_dir / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    if not manifest:
        print(f"{_display_path(manifest_path)} is missing or empty", file=stderr)
        return 2
    voices = _voice_entries(manifest)
    missing: list[str] = []
    unknown: list[str] = []
    for voice_id, recorded in voices.items():
        if voice_id not in CATALOG_VOICE_IDS:
            unknown.append(voice_id)
            continue
        if not isinstance(recorded, dict):
            continue
        for kind in KINDS:
            entry = recorded.get(kind)
            if not isinstance(entry, dict):
                continue
            target = greetings_dir / voice_id / f"{kind}.mp3"
            if not target.is_file():
                missing.append(_display_path(target))
                continue
            recorded[kind] = _entry_for(target, TEXTS[kind])
    if unknown or missing:
        if unknown:
            print(f"unknown voice id(s) in manifest: {', '.join(unknown)}", file=stderr)
        for path in missing:
            print(f"missing file: {path}", file=stderr)
        return 1
    manifest["version"] = MANIFEST_VERSION
    manifest["encoding"] = ENCODING
    manifest["texts"] = _current_texts()
    _write_manifest(manifest_path, manifest)
    print(f"Upgraded {_display_path(manifest_path)}", file=stdout)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-render greeting audio for every Deepgram catalog voice.")
    parser.add_argument("--live", action="store_true", help="call Deepgram TTS (default is a dry run)")
    parser.add_argument("--force", action="store_true", help="regenerate files even when the text hash is unchanged")
    parser.add_argument(
        "--voice",
        action="append",
        dest="voices",
        metavar="ID",
        help="only this voice id (repeatable; default is every catalog voice)",
    )
    parser.add_argument(
        "--upgrade-manifest",
        action="store_true",
        help="add per-clip text/settings fields to the existing manifest using the files on disk (no network)",
    )
    args = parser.parse_args(argv)
    if args.upgrade_manifest:
        return upgrade_manifest()
    try:
        return generate(live=args.live, force=args.force, voices=args.voices)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
