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
from collections.abc import Sequence
from dataclasses import dataclass
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
MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
GREETINGS_DIR = ROOT / "apps/web/static/greetings"
KINDS = ("full", "short")
TEXTS: dict[str, str] = {"full": INTRO["en"], "short": SHORT_GREETING["en"]}
MAX_ATTEMPTS = 3


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


def _text_entry(manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    texts = manifest.get("texts")
    if not isinstance(texts, dict):
        return {}
    entry = texts.get(kind)
    return entry if isinstance(entry, dict) else {}


def _voice_entries(manifest: dict[str, Any]) -> dict[str, Any]:
    voices = manifest.get("voices")
    return voices if isinstance(voices, dict) else {}


def _recorded_entry(manifest: dict[str, Any], voice_id: str, kind: str) -> dict[str, Any] | None:
    recorded = _voice_entries(manifest).get(voice_id)
    if not isinstance(recorded, dict):
        return None
    entry = recorded.get(kind)
    return entry if isinstance(entry, dict) else None


def _plan(voice_ids: Sequence[str], *, force: bool, greetings_dir: Path, manifest: dict[str, Any]) -> list[PlannedClip]:
    clips: list[PlannedClip] = []
    for voice_id in voice_ids:
        for kind in KINDS:
            text = TEXTS[kind]
            target = greetings_dir / voice_id / f"{kind}.mp3"
            recorded = _recorded_entry(manifest, voice_id, kind)
            unchanged = _text_entry(manifest, kind).get("sha256") == _text_sha256(text)
            skip = not force and target.is_file() and recorded is not None and unchanged
            clips.append(PlannedClip(voice_id=voice_id, kind=kind, text=text, target=target, skip=skip))
    return clips


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
    manifest_path = greetings_dir / MANIFEST_NAME
    voice_ids = _selected_voice_ids(voices)
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

    existing_voices = _voice_entries(manifest)
    selected = set(voice_ids)
    voices_out: dict[str, Any] = {
        voice_id: entry for voice_id, entry in existing_voices.items() if voice_id not in selected
    }
    failed: list[str] = []
    owned_client = client is None
    active_client = client if client is not None else httpx.Client(timeout=60.0)
    try:
        for voice_id in voice_ids:
            entries: dict[str, Any] = {}
            voice_failed = False
            for clip in (clip for clip in clips if clip.voice_id == voice_id):
                if clip.skip:
                    entries[clip.kind] = _recorded_entry(manifest, voice_id, clip.kind)
                    continue
                try:
                    audio = _synthesize(active_client, voice_id=voice_id, text=clip.text, api_key=api_key)
                except httpx.HTTPError as exc:
                    print(f"error: {voice_id}/{clip.kind}: {_describe_error(exc)}", file=stderr)
                    voice_failed = True
                    continue
                _write_bytes(clip.target, audio)
                entries[clip.kind] = {"bytes": len(audio), "sha256": _sha256(audio)}
                print(f"wrote {_display_path(clip.target)} ({len(audio)} bytes)", file=stdout)
            if voice_failed:
                failed.append(voice_id)
                continue
            voices_out[voice_id] = entries
    finally:
        if owned_client:
            active_client.close()

    new_manifest = {
        "version": MANIFEST_VERSION,
        "encoding": ENCODING,
        "texts": _current_texts(),
        "voices": voices_out,
    }
    _write_manifest(manifest_path, new_manifest)
    print(f"Manifest: {_display_path(manifest_path)}", file=stdout)
    if failed:
        print(f"Failed voices: {', '.join(failed)}", file=stderr)
        return 1
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
    args = parser.parse_args(argv)
    known_voices = {voice.id for voice in DEEPGRAM_VOICES}
    if args.voices:
        unknown = [voice_id for voice_id in args.voices if voice_id not in known_voices]
        if unknown:
            parser.error(f"unknown voice id(s): {', '.join(unknown)}")
    return generate(live=args.live, force=args.force, voices=args.voices)


if __name__ == "__main__":
    raise SystemExit(main())
