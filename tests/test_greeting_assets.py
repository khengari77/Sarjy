"""Offline tests for the greeting asset generator and the generated catalog."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_greetings as greetings  # noqa: E402
from app.domain.deepgram_voices import DEEPGRAM_VOICES  # noqa: E402
from app.domain.intro import INTRO, SHORT_GREETING  # noqa: E402

GREETINGS_DIR = ROOT / "apps/web/static/greetings"
MANIFEST = GREETINGS_DIR / "manifest.json"
KEY = "test-deepgram-key-not-real"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _audio_handler(requests: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, content=b"mp3-bytes")

    return handler


def _manifest(path: Path) -> dict:
    return json.loads((path / greetings.MANIFEST_NAME).read_text(encoding="utf-8"))


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(data: bytes, text: str) -> dict:
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "text_sha256": _text_hash(text),
        "encoding": greetings.ENCODING,
        "bit_rate": greetings.BIT_RATE,
    }


def _manifest_is_upgraded() -> bool:
    if not MANIFEST.exists():
        return False
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return manifest.get("version") == greetings.MANIFEST_VERSION


def test_dry_run_sends_nothing_and_reports_the_exact_character_total(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"dry run must not call the network: {request.url}")

    out = io.StringIO()
    with _client(handler) as client:
        code = greetings.generate(live=False, greetings_dir=tmp_path, client=client, out=out)

    expected_chars = len(DEEPGRAM_VOICES) * (len(INTRO["en"]) + len(SHORT_GREETING["en"]))
    assert code == 0
    assert f"Voices: {len(DEEPGRAM_VOICES)}" in out.getvalue()
    assert f"Total characters: {expected_chars}" in out.getvalue()
    assert "Dry run only" in out.getvalue()
    assert not list(tmp_path.iterdir())


def test_live_posts_the_right_request_for_each_voice_and_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    voices = ["aura-2-thalia-en", "aura-2-zeus-en"]
    requests: list[httpx.Request] = []
    out, err = io.StringIO(), io.StringIO()
    with _client(_audio_handler(requests)) as client:
        code = greetings.generate(live=True, voices=voices, greetings_dir=tmp_path, client=client, out=out, err=err)

    assert code == 0, err.getvalue()
    assert len(requests) == len(voices) * 2
    seen = set()
    for request in requests:
        assert request.method == "POST"
        assert request.url.scheme == "https"
        assert request.url.host == "api.deepgram.com"
        assert request.url.path == "/v1/speak"
        params = request.url.params
        assert params["encoding"] == "mp3"
        assert params["bit_rate"] == "48000"
        assert "container" not in params
        assert "sample_rate" not in params
        assert request.headers["authorization"] == f"Token {KEY}"
        body = json.loads(request.content)
        kind = "full" if body["text"] == INTRO["en"] else "short"
        assert body == {"text": greetings.TEXTS[kind]}
        seen.add((params["model"], kind))
    assert seen == {(voice, kind) for voice in voices for kind in ("full", "short")}
    assert KEY not in out.getvalue() + err.getvalue()

    manifest = _manifest(tmp_path)
    assert manifest["version"] == greetings.MANIFEST_VERSION
    assert manifest["encoding"] == "mp3"
    assert set(manifest["voices"]) == set(voices)
    for voice in voices:
        for kind, text in (("full", INTRO["en"]), ("short", SHORT_GREETING["en"])):
            data = (tmp_path / voice / f"{kind}.mp3").read_bytes()
            assert manifest["voices"][voice][kind] == _entry(data, text)
    for kind, text in (("full", INTRO["en"]), ("short", SHORT_GREETING["en"])):
        assert manifest["texts"][kind] == {"sha256": _text_hash(text), "chars": len(text)}


def test_live_skips_unchanged_files(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    voices = ["aura-2-thalia-en"]
    with _client(_audio_handler()) as client:
        assert greetings.generate(live=True, voices=voices, greetings_dir=tmp_path, client=client) == 0

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unchanged clips must not be regenerated: {request.url}")

    out = io.StringIO()
    with _client(handler) as client:
        code = greetings.generate(live=True, voices=voices, greetings_dir=tmp_path, client=client, out=out)

    assert code == 0
    assert "Files to write: 0" in out.getvalue()
    assert "Total characters: 0" in out.getvalue()


def test_text_change_regenerates_only_that_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    voice = "aura-2-thalia-en"
    with _client(_audio_handler()) as client:
        assert greetings.generate(live=True, voices=[voice], greetings_dir=tmp_path, client=client) == 0

    changed = INTRO["en"] + " One more line."
    monkeypatch.setitem(greetings.TEXTS, "full", changed)
    requests: list[httpx.Request] = []
    with _client(_audio_handler(requests)) as client:
        assert greetings.generate(live=True, voices=[voice], greetings_dir=tmp_path, client=client) == 0

    assert len(requests) == 1
    assert json.loads(requests[0].content) == {"text": changed}
    manifest = _manifest(tmp_path)
    assert manifest["texts"]["full"] == {"sha256": _text_hash(changed), "chars": len(changed)}
    assert manifest["texts"]["short"]["chars"] == len(SHORT_GREETING["en"])
    assert manifest["voices"][voice]["full"]["text_sha256"] == _text_hash(changed)


def test_partial_voice_run_leaves_other_entries_stale_then_regenerates(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    first, second = "aura-2-thalia-en", "aura-2-zeus-en"
    with _client(_audio_handler()) as client:
        assert greetings.generate(live=True, voices=[first, second], greetings_dir=tmp_path, client=client) == 0

    old_text_hash = _text_hash(INTRO["en"])
    changed = INTRO["en"] + " A changed line."
    monkeypatch.setitem(greetings.TEXTS, "full", changed)
    with _client(_audio_handler()) as client:
        assert greetings.generate(live=True, voices=[first], greetings_dir=tmp_path, client=client) == 0

    manifest = _manifest(tmp_path)
    assert manifest["voices"][first]["full"]["text_sha256"] == _text_hash(changed)
    assert manifest["voices"][second]["full"]["text_sha256"] == old_text_hash

    requests: list[httpx.Request] = []
    with _client(_audio_handler(requests)) as client:
        assert greetings.generate(live=True, voices=[first, second], greetings_dir=tmp_path, client=client) == 0

    assert len(requests) == 1
    assert requests[0].url.params["model"] == second
    assert json.loads(requests[0].content) == {"text": changed}
    assert _manifest(tmp_path)["voices"][second]["full"]["text_sha256"] == _text_hash(changed)


def test_corrupted_file_is_regenerated(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    voice = "aura-2-thalia-en"
    with _client(_audio_handler()) as client:
        assert greetings.generate(live=True, voices=[voice], greetings_dir=tmp_path, client=client) == 0

    target = tmp_path / voice / "full.mp3"
    target.write_bytes(b"truncated")
    requests: list[httpx.Request] = []
    with _client(_audio_handler(requests)) as client:
        assert greetings.generate(live=True, voices=[voice], greetings_dir=tmp_path, client=client) == 0

    assert len(requests) == 1
    assert target.read_bytes() == b"mp3-bytes"
    assert _manifest(tmp_path)["voices"][voice]["full"] == _entry(b"mp3-bytes", INTRO["en"])


def test_changed_encoding_settings_regenerate(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    voice = "aura-2-thalia-en"
    with _client(_audio_handler()) as client:
        assert greetings.generate(live=True, voices=[voice], greetings_dir=tmp_path, client=client) == 0

    monkeypatch.setattr(greetings, "BIT_RATE", "32000")
    requests: list[httpx.Request] = []
    with _client(_audio_handler(requests)) as client:
        assert greetings.generate(live=True, voices=[voice], greetings_dir=tmp_path, client=client) == 0

    assert len(requests) == 2
    assert all(request.url.params["bit_rate"] == "32000" for request in requests)
    manifest = _manifest(tmp_path)
    for kind in ("full", "short"):
        assert manifest["voices"][voice][kind]["bit_rate"] == "32000"


def test_429_retry_after_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    sleeps: list[float] = []
    monkeypatch.setattr(greetings.time, "sleep", sleeps.append)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, content=b"mp3-bytes")

    with _client(handler) as client:
        code = greetings.generate(live=True, voices=["aura-2-thalia-en"], greetings_dir=tmp_path, client=client)

    assert code == 0
    assert len(calls) == 4
    assert sleeps == [5.0, 5.0]


def test_persistent_429_fails_the_voice_after_bounded_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    sleeps: list[float] = []
    monkeypatch.setattr(greetings.time, "sleep", sleeps.append)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    err = io.StringIO()
    with _client(handler) as client:
        code = greetings.generate(
            live=True, voices=["aura-2-thalia-en"], greetings_dir=tmp_path, client=client, err=err
        )

    assert code == 1
    assert len(calls) == greetings.MAX_ATTEMPTS * 2
    assert sleeps == [greetings.MAX_RETRY_WAIT_S] * 4
    assert "HTTP 429" in err.getvalue()
    assert not (tmp_path / greetings.MANIFEST_NAME).exists()


def test_invalid_voice_id_raises_before_building_paths(tmp_path):
    with pytest.raises(ValueError, match="unknown voice id"):
        greetings.generate(live=False, voices=["../outside"], greetings_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_crash_mid_run_leaves_manifest_matching_completed_files(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    voices = ["aura-2-thalia-en", "aura-2-zeus-en", "aura-2-apollo-en"]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 5:
            raise RuntimeError("simulated crash")
        return httpx.Response(200, content=b"mp3-bytes")

    with pytest.raises(RuntimeError, match="simulated crash"), _client(handler) as client:
        greetings.generate(live=True, voices=voices, greetings_dir=tmp_path, client=client)

    manifest = _manifest(tmp_path)
    assert set(manifest["voices"]) == set(voices[:2])
    for voice in voices[:2]:
        assert set(manifest["voices"][voice]) == {"full", "short"}
    assert not (tmp_path / voices[2]).exists()


def test_upgrade_manifest_adds_fields_from_files_without_network(tmp_path, monkeypatch):
    voice = "aura-2-thalia-en"
    (tmp_path / voice).mkdir(parents=True)
    full = b"full-audio"
    short = b"short-audio"
    (tmp_path / voice / "full.mp3").write_bytes(full)
    (tmp_path / voice / "short.mp3").write_bytes(short)
    old = {
        "version": 1,
        "encoding": "mp3",
        "texts": {
            "full": {"sha256": _text_hash(INTRO["en"]), "chars": len(INTRO["en"])},
            "short": {"sha256": _text_hash(SHORT_GREETING["en"]), "chars": len(SHORT_GREETING["en"])},
        },
        "voices": {
            voice: {
                "full": {"bytes": len(full), "sha256": hashlib.sha256(full).hexdigest()},
                "short": {"bytes": len(short), "sha256": hashlib.sha256(short).hexdigest()},
            }
        },
    }
    (tmp_path / greetings.MANIFEST_NAME).write_text(json.dumps(old, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def no_network(*args, **kwargs):
        raise AssertionError("upgrade must not use the network")

    monkeypatch.setattr(greetings.httpx, "Client", no_network)
    assert greetings.upgrade_manifest(tmp_path) == 0

    manifest = _manifest(tmp_path)
    assert manifest["version"] == greetings.MANIFEST_VERSION
    assert manifest["voices"][voice]["full"] == _entry(full, INTRO["en"])
    assert manifest["voices"][voice]["short"] == _entry(short, SHORT_GREETING["en"])
    assert manifest["voices"][voice]["full"]["bytes"] == len(full)
    assert manifest["voices"][voice]["full"]["sha256"] == hashlib.sha256(full).hexdigest()


def test_failed_voice_is_not_recorded_and_exits_non_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", KEY)
    good, bad = "aura-2-thalia-en", "aura-2-zeus-en"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["model"] == bad:
            return httpx.Response(503)
        return httpx.Response(200, content=b"mp3-bytes")

    err = io.StringIO()
    with _client(handler) as client:
        code = greetings.generate(live=True, voices=[good, bad], greetings_dir=tmp_path, client=client, err=err)

    assert code == 1
    manifest = _manifest(tmp_path)
    assert set(manifest["voices"]) == {good}
    assert not (tmp_path / bad / "full.mp3").exists()
    assert not (tmp_path / bad / "short.mp3").exists()
    assert f"Failed voices: {bad}" in err.getvalue()


def test_api_key_never_appears_in_output(tmp_path, monkeypatch):
    secret = "secret-value-that-must-not-leak"
    monkeypatch.setenv("DEEPGRAM_API_KEY", secret)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    out, err = io.StringIO(), io.StringIO()
    with _client(handler) as client:
        code = greetings.generate(
            live=True, voices=["aura-2-thalia-en"], greetings_dir=tmp_path, client=client, out=out, err=err
        )

    assert code == 1
    assert secret not in out.getvalue() + err.getvalue()


@pytest.mark.skipif(
    not _manifest_is_upgraded(),
    reason="run `uv run python scripts/generate_greetings.py --upgrade-manifest` first",
)
def test_every_catalog_voice_has_both_greeting_assets():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for kind, text in (("full", INTRO["en"]), ("short", SHORT_GREETING["en"])):
        assert manifest["texts"][kind] == {"sha256": _text_hash(text), "chars": len(text)}

    for voice in DEEPGRAM_VOICES:
        for kind, text in (("full", INTRO["en"]), ("short", SHORT_GREETING["en"])):
            path = GREETINGS_DIR / voice.id / f"{kind}.mp3"
            assert path.is_file(), f"missing {path.relative_to(ROOT)}"
            data = path.read_bytes()
            entry = manifest["voices"][voice.id][kind]
            assert entry["bytes"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()
            assert entry["text_sha256"] == _text_hash(text)
            assert entry["encoding"] == greetings.ENCODING
            assert entry["bit_rate"] == greetings.BIT_RATE
