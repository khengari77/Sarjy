# Greeting audio assets

The browser plays a pre-rendered greeting when a call starts. The clips are
generated from the English text constants in
`apps/api/app/domain/intro.py`: `INTRO["en"]` (full) and
`SHORT_GREETING["en"]` (short). Arabic gets no greeting on Deepgram Aura-2, so
only English is rendered.

The worker does not use these files. They exist only to avoid synthesizing the
same greeting on every call.

## Output

`apps/web/static/greetings/<voice_id>/<kind>.mp3` for every voice id in
`apps/api/app/domain/deepgram_voices.py` (41 Aura-2 voices, including the
default `aura-2-thalia-en`), plus `apps/web/static/greetings/manifest.json`.

The manifest records the text hash and character count for each kind, and the
byte size and file hash for every rendered clip. It is written with sorted keys
so repeated runs produce stable output.

Files are MP3 at 48 kbps (`encoding=mp3`, `bit_rate=48000`); Deepgram fixes the
MP3 sample rate at 22050 Hz. Each clip is typically tens of kilobytes.

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
- `--force` regenerates even when the text hash is unchanged.

Re-runs are idempotent: a `(voice, kind)` pair is skipped when its file exists
and the manifest text hash matches the current constant. If a voice fails, it is
left out of the manifest and the command exits non-zero; the manifest is written
atomically at the end.

Regenerate whenever `INTRO["en"]`, `SHORT_GREETING["en"]` or the voice catalog
changes. The generated-asset test in `tests/test_greeting_assets.py` fails when
the checked-in assets drift from the text or catalog.

This generation was explicitly approved once for development and is not charged
to the production daily Deepgram character ledger (`deepgram_tts`); the script
does not reserve from that ledger.
