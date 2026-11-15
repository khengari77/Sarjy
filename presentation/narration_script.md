# Narration script

Read each block aloud at a calm pace. Times are estimates at about 2.3 words per second; the slide for each scene lasts the duration in `timing.json`. After recording, tell me the real length of each section and I will retime the scenes.

Parts: A Why, B How it works, D If I had more days. Part C (the live demo) is in `demo_script.md`.

## 01. Title (Part A)

_18 words, about 8 s. Scene length 9 s._

This is Sarjy, a voice assistant I built in four days. First, why I built it this way.

## 02. The assignment (Part A)

_36 words, about 16 s. Scene length 25 s._

The assignment asks for one deep dive, one area to go deep on.

I chose memory control. And alongside it, a second differentiator: voice commands for the user interface.

Let me explain both, starting with memory.

## 03. Why memory control (Part A)

_103 words, about 45 s. Scene length 50 s._

Why memory control? Every chatbot I tested remembers things, but gives me no fine-grained control over what is saved and what is not.

Sarjy asks first. It proposes a memory, I say yes, and only then is it saved. I can hide a memory, undo that, or delete it, with a confirmation.

Explicit consent is my proof of concept that memory can be controlled.

In a real product it would feel natural: the model itself would decide what to remember and what not to, under a smart policy I can tune. For example: off, ask first, or automatic. That part is future work.

## 04. Why voice UI commands (Part A)

_126 words, about 55 s. Scene length 60 s._

The second differentiator is voice commands for the interface.

Speech-to-text has become easy and cheap, and models under a hundred million parameters run smoothly on phones and laptops. So why has the user interface not caught up?

Between 2000 and 2010, Microsoft tried adding speech recognition to Windows and Word, and it failed, because recognition was immature. Today that is no longer true.

And commanding is the most natural way to interact: speak to your phone to open or close an app.

Sarjy is a mature proof of concept, not a finished product, built in four days. By voice you can switch between light and dark mode, change the speaking speed, switch between a man's and a woman's voice, show or hide captions, and play Wordle.

## 05. Three parts (Part B)

_72 words, about 31 s. Scene length 35 s._

Now, how it works. Sarjy has three parts.

A SvelteKit web app, which holds the microphone, the captions and the memory panel. A FastAPI service, which owns state: conversations, memory, commands and daily limits. And a LiveKit voice worker, which runs the speech pipeline.

The browser and the worker meet in a LiveKit room, for live audio and small data messages, and the worker talks to the API over an internal channel.

## 06. One voice turn (Part B)

_83 words, about 36 s. Scene length 45 s._

One voice turn. I speak, and Deepgram turns my speech into text.

When the sentence is finished, the worker asks the API what to do with it. The API checks whether it is a command, and otherwise assembles the context: recent history and my active memories.

The model answers, OpenAI or DeepSeek depending on the configuration. The text goes to text-to-speech, Deepgram Aura or ElevenLabs, and I hear the reply.

Every speech request is counted against a daily budget before it is synthesized.

## 07. Memory (Part B)

_105 words, about 46 s. Scene length 55 s._

Memory. When I ask Sarjy to remember something, the model can only propose it. Nothing is saved yet.

The application itself asks me for consent, and only my yes saves it.

Once saved, I stay in control. If I say forget, or click the eye icon, the memory is hidden in every conversation. Say undo, and it is back.

Deleting is permanent, so it needs a confirmation, and it is possible only in the panel, never by voice.

In a new conversation, my active memories are added to the context, so Sarjy can answer: what is my name. Hidden and deleted memories are never included.

## 08. Voice UI commands (Part B)

_105 words, about 46 s. Scene length 50 s._

Voice commands take a short path.

First, the API looks for an exact phrase, such as speak faster or hide captions. That is instant and calls no model.

If nothing matches, a small classifier called Jev reads the sentence in one call and answers a few questions: which setting, and which direction. Only when it is confident, at eighty-five percent, does the command run. If it is unsure, the sentence goes to the main model as normal conversation.

A command returns a spoken line and a typed action that the page applies: theme, captions, speed, or voice. A voice change applies on the next call.

## 09. Tools (Part B)

_58 words, about 25 s. Scene length 40 s._

The model can also call tools. Weather asks Open-Meteo for live data. The memory tools propose, forget and recall.

And Wordle: the model starts the game, submits my spoken guesses and reads the status. The game lives on the server, and the popup on the page just refreshes from it, so the answer never travels in the message.

## 10. Future: personas (Part D)

_45 words, about 20 s. Scene length 25 s._

If I had more days. First, controllable personas.

I could shape Sarjy's persona, a playful peer for example, with an instruction box, or by voice: become a playful peer. It would simply be added to Sarjy's instructions.

This is future work. It is not built.

## 11. Future: multi-agent calls (Part D)

_43 words, about 19 s. Scene length 30 s._

Second, multi-agent calls. Several agents, each with a different persona, in the same call.

A router sends my question to the most fitting one, and the others add their thoughts, so it becomes a three-way or four-way conversation.

Also future work, not built.

## 12. Closing (Part D)

_11 words, about 5 s. Scene length 6 s._

That is the idea. Now let me show you Sarjy live.

Total scene time: 430 s (7.2 min).
