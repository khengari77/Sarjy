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
    assert manifest["version"] == 1
    assert manifest["encoding"] == "mp3"
    assert set(manifest["voices"]) == set(voices)
    for voice in voices:
        for kind in ("full", "short"):
            data = (tmp_path / voice / f"{kind}.mp3").read_bytes()
            entry = manifest["voices"][voice][kind]
            assert entry == {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    for kind, text in (("full", INTRO["en"]), ("short", SHORT_GREETING["en"])):
        assert manifest["texts"][kind] == {
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "chars": len(text),
        }


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
    assert manifest["texts"]["full"] == {
        "sha256": hashlib.sha256(changed.encode("utf-8")).hexdigest(),
        "chars": len(changed),
    }
    assert manifest["texts"]["short"]["chars"] == len(SHORT_GREETING["en"])


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


@pytest.mark.skipif(not MANIFEST.exists(), reason="greeting assets have not been generated yet")
def test_every_catalog_voice_has_both_greeting_assets():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for kind, text in (("full", INTRO["en"]), ("short", SHORT_GREETING["en"])):
        assert manifest["texts"][kind] == {
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "chars": len(text),
        }

    for voice in DEEPGRAM_VOICES:
        for kind in ("full", "short"):
            path = GREETINGS_DIR / voice.id / f"{kind}.mp3"
            assert path.is_file(), f"missing {path.relative_to(ROOT)}"
            data = path.read_bytes()
            entry = manifest["voices"][voice.id][kind]
            assert entry["bytes"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()
