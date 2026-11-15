# Part C: Demo script

Perform each step on camera and say the quoted phrases aloud. "Expect" is what should happen; "Check" is what to look at. Items marked **check live** could not be confirmed from the code alone (exact spoken wording of replies, timing, deployment state).

Verified from the code: exact phrases and what they do (`apps/api/app/domain/commands.py`, `ui_routing.py`, `app/services/commands.py`, `jev_ui.py`), the consent flow (`memory_tools.py`, `memory_capture.py`, `voice_exclusions.py`), the panel buttons (`lib/memory/*`), the speed control (`lib/chat/VoiceSettings.svelte`, `services/voice-agent/provider_factory.py`).

## Pre-flight checklist

1. **Fresh identity.** Use a fresh invitation link (`/review#invitation=...`, see `docs/reviewer-access.md`) or a clean demo session, so the memory panel starts empty and "what is my age" has nothing to find. Each identity gets 3 call reservations in total, and one active call at a time; a failed dispatch also uses a reservation. Plan the recording in at most 3 calls, or have a second fresh invitation ready.
2. **Is a new call needed for "new conversation"?** Not verified. **Check live** in a rehearsal: does "New conversation" keep the call alive, or do you need End call then Start call? If it needs a call each time, the demo below uses: call 1 (steps 1 to 5), call 2 (step 6), call 3 (step 7). Everything else is done inside whichever call is open.
3. **Microphone.** Browser permission granted, headset on (avoids echo that makes Sarjy hear itself), quiet room. The mic indicator on the page should read "Microphone on".
4. **Provider caps that matter** (all from `README.md` and `.env.example`):
   - OpenAI model: $1/day, counted as 50 requests/day, tool continuations included. The full demo is about 30 to 35 turns; do not rehearse many full runs on the same day.
   - Speech synthesis ledger: Deepgram 50,000 characters/day by default; ElevenLabs only 2,500 characters/day. The limerick segment alone speaks the limerick 4 times. Confirm which `TTS_PROVIDER` is live; under ElevenLabs shorten the limerick segment.
   - Classifier (Jev): 2,000 requests/day, plenty.
   - A call lasts at most 10 minutes; the worker leaves 60 seconds after the browser disconnects. Agent cold start can take 10 to 20 seconds: start the call and wait for "Connected" before speaking.
5. **Feature switches.** Wordle needs `WORDLE_ENABLED=true` on the API (default is off) and a reachable puzzle source. The Wordle voice and model tools live on branch `agent/issue-110-wordle-voice` and may not be in the deployed release: **check live** that "Play Wordle" opens the popup before recording.
6. **Voice and speed.** The speed control only works for voices that support it (Deepgram English/Spanish voices, ElevenLabs range). Under Deepgram the speed range is 0.8 to 1.2 in 0.1 steps. Note the starting speed in the Controls tab; the script assumes 1.0.
7. **Rehearse once**, off camera, the three riskiest steps: Wordle, the forget and re-ask, and the voice change.
8. **Screen layout.** Keep the right panel open on the Memories tab for the memory steps, and switch to Controls for speed and captions. Tabs are called Memories, Controls, Transcript.

## If something fails mid-recording

- Do not edit the failure out of your commentary. Say what you expected, say "let me try that again", repeat the phrase once.
- If Sarjy did not hear you: check the Transcript tab. A wrong transcript means a recognition miss, so repeat more slowly. Do not keep retrying more than twice.
- If a call drops or "Start call" is refused: you may be out of call reservations or the global limit (2 concurrent). Wait, use the second invitation, and resume at the step you were on. Memory persists per identity, so a fresh identity needs you to redo the save steps.
- If a spoken command is not understood, use the exact phrase from the tables below (the exact-match path is the most reliable), or use the button in the panel, and say so on camera.
- If a provider cap is hit, speech stops with an explicit error. Stop recording; do not try to work around it (no resets, no top-ups). Resume after the Tripoli-time midnight reset.
- Record each numbered step as its own take, so one failure costs only one take.

## Steps

### 1. Start a conversation and speak

1. Say aloud: "First I will start a call and just talk to it."
2. Click the orb (or "Start call"). Wait for "Connected".
3. Say: **"Hello Sarjy, can you hear me?"**
- Expect: a short spoken reply; your words appear as captions and in the Transcript tab.
- Check: orb animates while listening and speaking; captions are visible.

### 2. Weather (external API)

Say: **"What is the weather in Tripoli?"** (Name the country if the city is ambiguous: "Paris, France".)
- Expect: Sarjy answers with live current weather for that city. For an ambiguous city it asks which one.
- Check: a tool chip may appear in the transcript; the numbers look plausible (Open-Meteo, called by the API).

### 3. Wordle by voice (live popup) **check live**

1. Say: **"Play Wordle."**
   - Expect: the Wordle popup opens with an empty board for today's puzzle.
2. Say: **"Guess crane."** Then a second guess, for example **"Guess slate."**
   - Expect: each guess fills a row in the popup with colored feedback; Sarjy reads back the result.
   - Check: the board is the same one you would get by typing; the answer is never spoken during play.
3. Say: **"How many guesses do I have left?"**
4. Say: **"Close Wordle"** (or "Back to chat").
   - Expect: popup closes, the call continues.
- If the model path is not deployed, the exact-phrase path ("Play Wordle", "Guess crane", "Quit Wordle") is the alternative. Either one is fine on camera; say which you are showing.

### 4. Save the name and the age (explicit consent)

One memory at a time; each needs its own yes.
1. Say: **"Remember that my name is <your name>."** (use the same name each take)
   - Expect: Sarjy does not say "saved". It asks: "Should I remember this: ...?"
   - Check: the Memories tab shows nothing new yet.
2. Say: **"Yes."**
   - Expect: Sarjy confirms it is saved; the memory appears in the Memories tab.
3. Say: **"Remember that my age is <age>."** Wait for the question, then **"Yes."**
   - Expect: same flow; two memories listed.
- Say "no" in one take if you want to show a refusal: nothing is saved.

### 5. New conversation: recall the name

1. Click **New conversation** (left sidebar). Start a call if needed (see pre-flight item 2).
2. Say: **"What is my name?"**
   - Expect: it answers with your name. This works because your active memories are added to every new conversation's context.

### 6. Forget the age, then check in another new conversation

1. Say: **"Forget my age."**
   - Expect: immediate hide, no consent question. Sarjy says roughly "Hidden '...'. Say undo to bring it back."
   - Check: the age memory now shows as Hidden in the panel.
2. Click **New conversation** again.
3. Say: **"What is my age?"**
   - Expect: Sarjy says it does not know. Hidden memories are blocked in every conversation.
4. Say the other half of the contrast: **"What is my name?"** still works.

### 7. Speed demo

Open the Controls tab so the speed value is visible.
1. Say: **"Recite a famous limerick."** Listen to it at normal speed.
2. Say: **"Speak faster."**
   - Expect: a short spoken acknowledgment; the Controls speed select moves to 1.1. The speed takes effect on the next reply, not the current one.
3. Say: **"Repeat the limerick."** (It is faster.)
4. Say: **"Speak faster."** again, then **"Repeat the limerick."** (Faster again; 1.2 is the maximum.)
5. Set the speed to **1** manually in the Controls speed select.
   - Check: the select shows 1; the next reply is back at normal pace.
6. Say: **"Speak slower."** then **"Say the limerick again."**
   - Expect: slower than normal (0.9).
- Notes: other phrasings ("talk a bit quicker", "you are speaking too fast") go through the classifier and should work, but each is a miss risk: **check live** before using them on camera.

### 8. The rest of the UI by voice

| Say | Expect | Status |
|---|---|---|
| **"Dark mode"** then **"Light mode"** | Theme switches immediately in this browser | exact phrase, verified in code |
| **"Make it brighter"** or **"could you bring back dark mode?"** | Same, via the classifier | check live |
| **"Hide the captions"** then **"Show the captions"** | Captions disappear / return; the Show captions checkbox in Controls follows | exact phrase, verified |
| **"I want a man's voice"** | Sarjy says the voice is set "for the next call"; it does not change mid-call | exact phrase, verified |
| **"Use a woman's voice"** | Same; End call, Start call, then listen to the new voice | exact phrase, verified |
| **"Open my previous conversations"** | The history opens | exact phrase, verified |
| **"Open the memory explorer"** | The Memories panel opens | exact phrase, verified |
| **"Dark mode and hide the captions"** | "I can only make one change at a time. Which one should I do first?" Shows that compound requests are refused honestly | check live (classifier path) |

Voice gender depends on the voice catalog having an approved male and female voice; otherwise Sarjy says it has none and changes nothing. **Check live** which voices are listed.

### 9. Memory panel controls

Open the Memories tab with at least one memory in it (redo step 4 on a fresh identity if needed).
1. **Hide (eye icon)**: click it on a memory. Expect: row shows Hidden and the text "Sarjy does not use this memory anywhere while it is hidden." The same button flips to Restore.
2. **Undo**: click the button again (Restore memory). Expect: back to Stored. By voice: say **"forget my name"** then **"undo"** right away.
3. **Delete (trash icon)**: click it. Expect: a dialog "Delete this memory?" with the quoted text, stating it is permanent and only possible here, never by voice. Click **Cancel** first to show it is safe, then repeat and click **Delete**. Expect: the memory disappears and cannot be restored.
4. Say on camera: "Hide is reversible and works by voice or button. Delete is permanent, so it needs a confirmation and is only in the panel."

### 10. Arabic

The language switch is hidden in this release and new voice sessions start in English. Arabic speech works only if the TTS provider supports it: with the default Deepgram Aura-2, an Arabic voice session fails with `TTS_LANGUAGE_UNSUPPORTED`. Under ElevenLabs it is experimental and unvalidated. Recommended: do not demo Arabic on camera; say "Arabic is experimental and not enabled in this release" and move on. **Check live** only if you have an ElevenLabs configuration and want to try.

## Closing line

"Everything you just saw by voice, the theme, speed, captions, the game and the memory, goes through the same two ideas: the model proposes, and the application and the user decide."
