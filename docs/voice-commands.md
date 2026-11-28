# Voice call controls

These exact phrases are whole-utterance commands. Case, trailing punctuation and
polite wrappers such as "please" or "can you" are ignored; a longer sentence that
mentions one of them is ordinary conversation. They are outside the Jev
classifier and never change a setting.

| Command | Accepted phrases | Spoken reply | Effect |
| --- | --- | --- | --- |
| End the call | "end the call", "end call", "end this call", "hang up", "stop the call" | "Ending the call." | Ends the live call after the reply finishes speaking (6 s cap). |
| Mute the microphone | "mute", "mute the mic", "mute the microphone", "mute my mic", "mute my microphone", "mute myself", "mute me", "turn off the mic", "turn off the microphone", "turn the mic off", "turn the microphone off" | "Muted. Tap the microphone to unmute." | Mutes a live, unmuted microphone once. If already muted it replies "The microphone is already muted." and never toggles it back on. |
| New conversation | "new conversation", "new chat", "start a new conversation", "start a new chat", "open a new conversation", "open a new chat", "start a fresh conversation", "start a fresh chat" | "Starting a new conversation." | Ends a live call after the reply, creates and selects an empty chat, and reconnects with the microphone if a call was live. |

There is deliberately no voice "unmute" command: a muted microphone is not
published, so the agent cannot hear it. "Goodbye", "bye", "thanks", "end", "stop",
"end the conversation" and "start over" are not call-control commands; "start
over" belongs to Wordle.
