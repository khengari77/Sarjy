"""Writes narration_script.md from the text below and reports pace against timing.json.

usage: python make_script.py            # write script, print word counts vs scene durations
       python make_script.py --apply    # also set timing.json to the estimated read time
Pace assumption: 2.3 words per second (calm speech), plus 1.5 s of breathing room.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
WPS = 2.3

SCENES = [
    (
        "s01_title",
        "Title",
        "Part A",
        [
            "This is Sarjy, a voice assistant I built in four days. First, why I built it this way.",
        ],
    ),
    (
        "s02_assignment",
        "The assignment",
        "Part A",
        [
            "The assignment asks for one deep dive, one area to go deep on.",
            "I chose memory control. And alongside it, a second differentiator: voice commands for the user interface.",
            "Let me explain both, starting with memory.",
        ],
    ),
    (
        "s03_why_memory",
        "Why memory control",
        "Part A",
        [
            "Why memory control? Every chatbot I tested remembers things, but gives me no fine-grained control over what is saved and what is not.",
            "Sarjy asks first. It proposes a memory, I say yes, and only then is it saved. I can hide a memory, undo that, or delete it, with a confirmation.",
            "Explicit consent is my proof of concept that memory can be controlled.",
            "In a real product it would feel natural: the model itself would decide what to remember and what not to, under a smart policy I can tune. For example: off, ask first, or automatic. That part is future work.",
        ],
    ),
    (
        "s04_why_voice_ui",
        "Why voice UI commands",
        "Part A",
        [
            "The second differentiator is voice commands for the interface.",
            "Speech-to-text has become easy and cheap, and models under a hundred million parameters run smoothly on phones and laptops. So why has the user interface not caught up?",
            "Between 2000 and 2010, Microsoft tried adding speech recognition to Windows and Word, and it failed, because recognition was immature. Today that is no longer true.",
            "And commanding is the most natural way to interact: speak to your phone to open or close an app.",
            "Sarjy is a mature proof of concept, not a finished product, built in four days. By voice you can switch between light and dark mode, change the speaking speed, switch between a man's and a woman's voice, show or hide captions, and play Wordle.",
        ],
    ),
    (
        "s05_architecture",
        "Three parts",
        "Part B",
        [
            "Now, how it works. Sarjy has three parts.",
            "A SvelteKit web app, which holds the microphone, the captions and the memory panel. A FastAPI service, which owns state: conversations, memory, commands and daily limits. And a LiveKit voice worker, which runs the speech pipeline.",
            "The browser and the worker meet in a LiveKit room, for live audio and small data messages, and the worker talks to the API over an internal channel.",
        ],
    ),
    (
        "s06_pipeline",
        "One voice turn",
        "Part B",
        [
            "One voice turn. I speak, and Deepgram turns my speech into text.",
            "When the sentence is finished, the worker asks the API what to do with it. The API checks whether it is a command, and otherwise assembles the context: recent history and my active memories.",
            "The model answers, OpenAI or DeepSeek depending on the configuration. The text goes to text-to-speech, Deepgram Aura or ElevenLabs, and I hear the reply.",
            "Every speech request is counted against a daily budget before it is synthesized.",
        ],
    ),
    (
        "s07_memory",
        "Memory",
        "Part B",
        [
            "Memory. When I ask Sarjy to remember something, the model can only propose it. Nothing is saved yet.",
            "The application itself asks me for consent, and only my yes saves it.",
            "Once saved, I stay in control. If I say forget, or click the eye icon, the memory is hidden in every conversation. Say undo, and it is back.",
            "Deleting is permanent, so it needs a confirmation, and it is possible only in the panel, never by voice.",
            "In a new conversation, my active memories are added to the context, so Sarjy can answer: what is my name. Hidden and deleted memories are never included.",
        ],
    ),
    (
        "s08_ui_commands",
        "Voice UI commands",
        "Part B",
        [
            "Voice commands take a short path.",
            "First, the API looks for an exact phrase, such as speak faster or hide captions. That is instant and calls no model.",
            "If nothing matches, a small classifier called Jev reads the sentence in one call and answers a few questions: which setting, and which direction. Only when it is confident, at eighty-five percent, does the command run. If it is unsure, the sentence goes to the main model as normal conversation.",
            "A command returns a spoken line and a typed action that the page applies: theme, captions, speed, or voice. A voice change applies on the next call.",
        ],
    ),
    (
        "s09_tools",
        "Tools",
        "Part B",
        [
            "The model can also call tools. Weather asks Open-Meteo for live data. The memory tools propose, forget and recall.",
            "And Wordle: the model starts the game, submits my spoken guesses and reads the status. The game lives on the server, and the popup on the page just refreshes from it, so the answer never travels in the message.",
        ],
    ),
    (
        "s10_personas",
        "Future: personas",
        "Part D",
        [
            "If I had more days. First, controllable personas.",
            "I could shape Sarjy's persona, a playful peer for example, with an instruction box, or by voice: become a playful peer. It would simply be added to Sarjy's instructions.",
            "This is future work. It is not built.",
        ],
    ),
    (
        "s11_multi_agent",
        "Future: multi-agent calls",
        "Part D",
        [
            "Second, multi-agent calls. Several agents, each with a different persona, in the same call.",
            "A router sends my question to the most fitting one, and the others add their thoughts, so it becomes a three-way or four-way conversation.",
            "Also future work, not built.",
        ],
    ),
    (
        "s12_closing",
        "Closing",
        "Part D",
        [
            "That is the idea. Now let me show you Sarjy live.",
        ],
    ),
]


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'-]+", text))


def main() -> None:
    timing = json.loads((HERE / "timing.json").read_text())
    out = [
        "# Narration script",
        "",
        "Read each block aloud at a calm pace. Times are estimates at about 2.3 words per second; "
        "the slide for each scene lasts the duration in `timing.json`. After recording, tell me the real "
        "length of each section and I will retime the scenes.",
        "",
        "Parts: A Why, B How it works, D If I had more days. Part C (the live demo) is in `demo_script.md`.",
        "",
    ]
    total = 0.0
    for i, (key, name, part, paras) in enumerate(SCENES, 1):
        n = sum(words(p) for p in paras)
        est = n / WPS
        total += timing[key]
        flag = "" if est + 0.5 <= timing[key] else "  (LONGER than scene: raise timing)"
        out += [
            f"## {i:02d}. {name} ({part})",
            "",
            f"_{n} words, about {est:.0f} s. Scene length {timing[key]} s._{flag}",
            "",
        ]
        out += [p + "\n" for p in paras]
        print(f"{key:20s} words={n:3d} est={est:5.1f}s scene={timing[key]}s{flag}")
        if "--apply" in sys.argv:
            timing[key] = round(est + 1.5)
    if "--apply" in sys.argv:
        (HERE / "timing.json").write_text(json.dumps(timing, indent=2) + "\n")
        total = sum(v for k, v in timing.items() if not k.startswith("_"))
    out += [f"Total scene time: {total:.0f} s ({total / 60:.1f} min).", ""]
    (HERE / "narration_script.md").write_text("\n".join(out))
    print(f"total {total:.0f}s")


main()
