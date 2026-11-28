# Greeting audio assets

The web player that plays a pre-rendered greeting at call start is a separate
follow-up PR; the files are not wired into the app yet. The worker does not use
these files. They exist only so a later client can avoid synthesizing the same
greeting on every call.

The clips are generated from the English text constants in
`apps/api/app/domain/intro.py`: `INTRO["en"]` (full) and
`SHORT_GREETING["en"]` (short). Arabic gets no greeting on Deepgram Aura-2, so
only English is rendered.

## Output

`apps/web/static/greetings/<voice_id>/<kind>.mp3` for every voice id in
`apps/api/app/domain/deepgram_voices.py` (41 Aura-2 voices, including the
default `aura-2-thalia-en`), plus `apps/web/static/greetings/manifest.json`.

The manifest keeps a global `texts` block (text hash and character count for
each kind) for the drift check, and a per-clip entry for every rendered file:
`bytes`, `sha256`, `text_sha256`, `encoding` and `bit_rate`. It is written with
sorted keys after every completed clip, using a temporary file and rename, so it
never records a file that is not already on disk.

Files are requested as MP3 at 48 kbps (`encoding=mp3`, `bit_rate=48000`).
Deepgram documents the MP3 sample rate as fixed at 22.05 kHz, while `file` on
the generated clips reports MPEG-2 layer III, 24 kHz, mono, 48 kbps; both play
in browsers. Each clip is typically tens of kilobytes.

## Regenerate

The script reads `DEEPGRAM_API_KEY` from the environment at run time. It never
prints the key.

Dry run (default, makes no API calls; prints voices, files and the exact
character total):

```sh
uv run python scripts/generate_greetings.py
```

Live one-time development generation:

```sh
DEEPGRAM_API_KEY=... uv run python scripts/generate_greetings.py --live
```

Useful options:

- `--voice aura-2-thalia-en` restricts the run to one voice (repeatable).
- `--force` regenerates even when the recorded file and text are unchanged.

Re-runs are idempotent per `(voice, kind)`: a clip is skipped only when its file
size and hash match the manifest entry, the entry's `text_sha256` matches the
current text, and `encoding`/`bit_rate` match the current settings. A partial
`--voice` run therefore leaves the other voices marked stale instead of
silently skipping them forever; a later full run regenerates them.

If a voice fails, it exits non-zero and the failed clips are not recorded.
Timeouts, 5xx and 429 responses are retried up to three attempts total; 429
waits use `Retry-After`, capped at 30 seconds.

Regenerate whenever `INTRO["en"]`, `SHORT_GREETING["en"]` or the voice catalog
changes. The generated-asset test in `tests/test_greeting_assets.py` fails when
the checked-in assets drift from the text, catalog or recorded settings.

This generation was explicitly approved once for development and is not charged
to the production daily Deepgram character ledger (`deepgram_tts`); the script
does not reserve from that ledger.

## Upgrading an older manifest

A manifest written before per-clip text/settings fields existed can be upgraded
in place from the files already on disk, without any network call:

```sh
uv run python scripts/generate_greetings.py --upgrade-manifest
```

This preserves each clip's `bytes` and `sha256`, adds `text_sha256`, `encoding`
and `bit_rate`, and updates the global `texts` block.
