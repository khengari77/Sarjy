# Sarjy presentation plan

Manim Community 0.22.1, 1080p60 final, no audio. Per-scene durations live in `timing.json`;
each scene syncs its beats to fractions of that duration, so retiming = edit JSON, re-render.

## Scenes (nominal durations, seconds)

| # | File / class | Part | Shows | s |
|---|---|---|---|---|
| 01 | `s01_title` | - | Title card: Sarjy, Voice Context Studio | 8 |
| 02 | `s02_assignment` | A | Assignment lists deep-dive options; "Memory control" chosen, plus voice UI commands | 25 |
| 03 | `s03_why_memory` | A | Chatbots: save everything / nothing, no control vs Sarjy: propose, yes, hide, undo, delete; policy dial off / ask first / automatic marked "future" | 50 |
| 04 | `s04_why_voice_ui` | A | Speech-to-text cheap, small models on devices, but UI not voice-driven; Windows speech 2000-2010; "open an app" is natural; what Sarjy can do by voice | 55 |
| 05 | `s05_architecture` | B | Three parts: SvelteKit web app, FastAPI API, LiveKit worker, and the external providers | 35 |
| 06 | `s06_pipeline` | B | One voice turn: mic, STT, API turn/start, command check, model, TTS | 45 |
| 07 | `s07_memory` | B | Propose, consent, save; hide, undo, delete; recall in a new conversation | 55 |
| 08 | `s08_ui_commands` | B | Command path: exact phrases, then Jev classifier, else main model; effects over data channel | 50 |
| 09 | `s09_tools` | B | Model-driven tools: weather, memory tools, Wordle with live popup | 40 |
| 10 | `s10_personas` | D | Future work: controllable personas | 25 |
| 11 | `s11_multi_agent` | D | Future work: multi-agent calls with router | 30 |
| 12 | `s12_closing` | - | Closing card | 6 |

Total about 424 s (7:04). Part C (demo) is not a scene: it is `demo_script.md`, performed live.

## Verified repo facts (file references)

Three parts and providers
- README.md line 3: "SvelteKit UI, FastAPI state service, and LiveKit Python speech worker". Web proxies `/api/v1/*` to the API (README "Local development"). Worker: `services/voice-agent/main.py`.
- STT Deepgram Nova-3; LLM `openai_direct` gpt-6-luna or `deepseek_direct` deepseek-flash; TTS Deepgram Aura-2 (default `tts_provider=deepgram`, `apps/api/app/config.py:31`) or ElevenLabs Flash v2.5. `apps/api/app/domain/pipeline.py` (PRESET, LLM_CHOICES, DEEPGRAM_TTS), `services/voice-agent/provider_factory.py` (`build_providers`). The default LLM provider differs between README (OpenAI) and `config.py:23` (deepseek); slides therefore say "OpenAI or DeepSeek".
- Worker session: `AgentSession(vad=silero.VAD.load(), preemptive_generation=False, ...)` (main.py ~850).
- Aura-2 has no Arabic; Arabic under Deepgram fails with `TTS_LANGUAGE_UNSUPPORTED` (pipeline.py `validate_pipeline`, README). Language switch is hidden in the UI; Arabic is experimental.

Turn flow (`services/voice-agent/main.py`)
- `compile()` posts `/internal/agent/turn/start` with the finalized transcript; the API assembles context (history, active memories) and returns it plus turn id.
- `llm_node()`: first `command_for()` -> `/internal/agent/command`; if a command applies, its deterministic spoken line is the whole reply and the effect is published to the UI (`_publish_command`); else forced memory question; else the model request (`snapshot_request`) with tools. Replies pass `tts_node`, which reserves characters against a daily TTS ledger.
- Speed is applied at the next turn's synthesis boundary (`_apply_speaking_speed`), so "speak faster" is audible on the next reply.

Voice UI commands (`docs/jev-ui-commands.md`, `app/domain/ui_routing.py`, `app/domain/commands.py`, `app/services/commands.py`, `app/services/jev_ui.py`)
- Order inside the API: memory handling, exact-phrase grammar, voice-name/gender grammar, then Jev, then unsupported fallback. An exact match never calls Jev. NOTE: this is the reverse of the brief ("fast-path only as safety net"); the slides follow the code: exact phrases first, Jev handles paraphrases, main model is the fall-through.
- Jev: one `POST /v1/systemone` with seven choice questions (route, family, theme, captions, speed, voice, memory_op); acts only when family and detail are >= 0.85 and agree and route is not a confident "conversation". 1200 ms deadline; any failure falls through to the main model.
- Actions: SetTheme (light/dark/system), SetCaptionsVisible, AdjustSpeakingSpeed (+-0.1, range 0.8-1.2), voice male/female; "two changes at once" gets "I can only make one change at a time". Voice change applies to the NEXT call ("Voice set to X for the next call", services/commands.py:173). Theme is browser-local.
- Also exact phrases: open conversation history, open memory explorer.
- memory_op from the same Jev call forces `propose_memory` / `propose_forget` / `recall` as the first tool choice of the turn.

Memory (`services/memory_tools.py`, `memory_capture.py`, `voice_exclusions.py`, `memory_visibility.py`, `context_assembly.py`)
- Propose-only tools: `propose_memory(fact)` returns a signed proposal, nothing is saved; the app speaks "Should I remember this: ...?" deterministically; consent is armed only after that speech played out; a spoken yes/no (`_YES`/`_NO` in commands.py) resolves it. Save is by the application, not the model.
- Forget by voice = hide: immediate, memory-level (all conversations), reversible; app says "Hidden '...'. Say undo to bring it back." (voice_exclusions.py:404). "undo" restores.
- Delete = permanent tombstone, only from the authenticated UI with a confirmation dialog ("Delete this memory? ... never by voice"), `DeleteMemoryDialog.svelte`, `memory_visibility.py`.
- Memory panel: one toggle button Hide / Restore (eye), plus Delete (`MemoryIconButton.svelte`).
- New conversation recall: `context_assembly.py` reads the owner's newest active memories (limit 200); hidden/deleted are blocked in every conversation (`memory_eligibility.py`). `recall` tool is a read-only owner-scoped lookup (5 results).
- NOT BUILT: the off / ask first / automatic sensitivity policy. grep finds no such setting. Shown only as "future" in slides.

Tools
- `get_current_weather` (Open-Meteo, server-side, `/internal/agent/tool/weather`), `propose_memory`, `propose_forget`, `recall` (main.py ~725-806).
- Wordle: server-owned game (`docs/wordle.md`, `routes/wordle.py`). Model-driven tools `play_wordle`, `submit_wordle_guess`, `wordle_status`, `close_wordle` and the live popup (data topic `sarjy.wordle`; client refetches the board, so the answer never travels in the message) exist on PR #129 (earlier branch `origin/agent/issue-110-wordle-voice`), being changed there (commits 26e5e45, 38e747f ...), not on main/this branch as of this work. Slides describe them as the #110 design; flag in report.

## Part D (future work, not built)
Persona control and multi-agent calls: no code in the repo (grep). Slides label them "future".

## Visual style
Background #0F1419; text #E2E8F0; muted #8A98A8; accents teal #4FD1C5 (Sarjy/user path), amber #F6AD55 (consent/attention), violet #9F7AEA (models/classifier), red #FC8181 (delete/failed). Noto Sans, >= 30 px equivalent text, one idea per screen, ease-in-out animation.

## Re-rendering and retiming

```sh
cd presentation
python3 -m venv .venv && .venv/bin/pip install manim==0.22.1 ruff==0.17.5   # once; needs cairo/pango (present)
./render.sh -ql                  # fast 480p15 preview of everything
./render.sh -qh                  # final 1080p60 (JOBS=3 parallel scenes by default)
./render.sh -qh s07_memory       # one scene, then re-concatenate whatever exists
```

Outputs land in `renders/<res>/` (git-ignored). Retime by editing `timing.json` (seconds per scene) and re-rendering; beats are placed at fractions of each scene's duration, so they scale. A scene prints a WARNING if its fixed animations are longer than its slot (minimum durations are roughly 14 s for s02 and 25 s for s03/s04 etc.; the log in `media/<scene>.log` shows it). `python make_script.py` rewrites `narration_script.md` and prints words per scene against the slot.

Merging the user's narration later: `ffmpeg -i renders/1080p60/sarjy_presentation.mp4 -i narration.wav -c:v copy -c:a aac out.mp4`, after retiming each scene to the recorded section lengths.
