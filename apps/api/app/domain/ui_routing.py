"""Pure Jev UI-command question wording, response validation and decision tree.

One request carries six small choice questions; code walks the answers.  An
action is produced only when the ``family`` answer and that family's detail answer
are each at least :data:`CONFIDENCE_THRESHOLD` and agree, and ``route`` is not a
confident ``conversation`` (``multiple_commands`` needs a confident route).
Anything else yields ``None`` so the turn falls through to the main model.  No
provider I/O happens here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import Field

from app.domain.contracts import (
    Action,
    AdjustSpeakingSpeed,
    Contract,
    SetCaptionsVisible,
    SetTheme,
)
from app.domain.routing import JEV_MODEL

CONFIDENCE_THRESHOLD: Final = 0.85
MAX_UI_COMMAND_CHARS: Final = 160
MAX_UI_COMMAND_WORDS: Final = 25
SPEED_STEP: Final = 0.1
# memory_op answers that force the matching typed tool on the first model inference; restore is never forced.
FORCED_MEMORY_TOOLS: Final[dict[str, str]] = {
    "remember": "propose_memory",
    "forget": "propose_forget",
    "recall": "recall",
}
MULTIPLE_COMMANDS_SPOKEN: Final = "I can only make one change at a time. Which one should I do first?"
# A confident "open Wordle" answer becomes this command detail; the worker runs the real play.
WORDLE_OPEN_DETAIL: Final = "WORDLE_OPEN"
WORDLE_OPEN_SPOKEN: Final = 'To open today\'s Wordle, say "play Wordle".'

_SCOPE = (
    "Judge only the current instruction itself. A polite request or an ability question that asks the "
    "assistant to do it ('can you', 'could you', 'would you', 'please', 'bring back', 'go back to') IS a direct "
    "request. Not requests: negations ('don't switch to dark mode'), statements of preference ('I love dark "
    "mode'), quotations, questions about a feature ('is dark mode better for your eyes?', 'what are captions?'), "
    "someone else's behaviour ('my brother talks too fast') and edits to the user's own text ('make the text "
    "darker in my essay')."
)

QUESTION_OPTIONS: Final[dict[str, tuple[str, ...]]] = {
    "route": ("conversation", "ui_command", "multiple_commands"),
    "family": ("theme", "captions", "speed", "voice", "wordle", "none"),
    "theme": ("light", "dark", "system", "none"),
    "captions": ("show", "hide", "none"),
    "speed": ("faster", "slower", "none"),
    "voice": ("male", "female", "named", "other", "none"),
    "memory_op": ("remember", "forget", "recall", "restore", "none"),
}

UI_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    "route": {
        "type": "choice",
        "instructions": (
            "Is the current instruction a request, including a polite one such as 'can you ...?', for the assistant to "
            "change one of its own application settings or open the daily Wordle game right now? " + _SCOPE
        ),
        "criteria": {
            "conversation": (
                "Anything that does not ask the assistant to change a setting now: chat, statements, opinions, "
                "negated or quoted requests, questions about how a feature works, other people's behaviour."
            ),
            "ui_command": (
                "One request, direct or polite ('can you make it dark?', 'could you bring back dark mode?'), to change "
                "exactly one setting: the theme, caption visibility, speaking speed or the assistant's voice; or a "
                "request to start, resume or open the daily Wordle game."
            ),
            "multiple_commands": "Direct requests to change two or more settings in the same instruction.",
        },
    },
    "family": {
        "type": "choice",
        "instructions": "Which setting does the current instruction ask the assistant to change, or does it ask to "
        "open the daily Wordle game, even politely? " + _SCOPE,
        "criteria": {
            "theme": "A request to change the appearance: light or dark mode, brighter or darker, bringing back a theme.",
            "captions": "A request to show, hide or turn on or off captions, subtitles or what the assistant says as text.",
            "speed": "A request to make the assistant speak faster or slower, including a complaint that it is too fast or slow.",
            "voice": "A request to change which voice the assistant speaks with.",
            "wordle": (
                "A request to start, resume or open the daily Wordle game now. Not talk about Wordle, past games, "
                "opinions or questions about it."
            ),
            "none": "Does not ask to change any of these settings.",
        },
    },
    "theme": {
        "type": "choice",
        "instructions": "Which theme does the current instruction ask the assistant to switch to, even politely or as 'bring back' or 'go back to'? "
        + _SCOPE,
        "criteria": {
            "light": "Asks for light mode, a light, bright theme, or to bring back or go back to it.",
            "dark": "Asks for dark mode, a dark theme, or to bring back or go back to it.",
            "system": "Asks to follow the system or device theme.",
            "none": "Does not ask to change the theme.",
        },
    },
    "captions": {
        "type": "choice",
        "instructions": "Does the current instruction ask, even politely, to show or hide captions? " + _SCOPE,
        "criteria": {
            "show": "Asks to turn captions or subtitles on, show them, or to see what the assistant says.",
            "hide": "Asks to turn captions or subtitles off or hide them.",
            "none": "Does not ask to change captions.",
        },
    },
    "speed": {
        "type": "choice",
        "instructions": "Does the current instruction ask, even politely, for the assistant to speak faster or slower? "
        + _SCOPE,
        "criteria": {
            "faster": "Asks to speak faster, speed up or talk quicker, or says it speaks too slowly.",
            "slower": "Asks to speak slower, slow down or talk more slowly, or says it speaks too fast.",
            "none": "Does not ask to change the assistant's speaking speed.",
        },
    },
    "voice": {
        "type": "choice",
        "instructions": "Which voice does the current instruction ask the assistant to use, even politely? " + _SCOPE,
        "criteria": {
            "male": "Asks for a male or man's voice.",
            "female": "Asks for a female or woman's voice.",
            "named": "Asks for a voice identified by a personal name.",
            "other": "Asks for a voice defined in any other way, such as accent, age or tone.",
            "none": "Does not ask to change the voice.",
        },
    },
    "memory_op": {
        "type": "choice",
        "instructions": (
            "Does the current instruction ask the assistant to save, forget, look up or restore something in its "
            "long-term memory about the user? Judge only the instruction itself."
        ),
        "criteria": {
            "remember": "Asks to remember, save, store, keep in mind or note a fact about the user for later, including a "
            "polite 'could you ...' request. Needs a concrete fact. Not 'remember to <task>', 'remember when ...', 'remember me' with no fact, or reminders.",
            "forget": (
                "Asks the assistant to forget, delete, remove, scrap, drop, erase, disregard or stop using something "
                "it remembers or was told earlier about the user ('scrap what I said about tea', 'wipe everything you "
                "know about me'). Not the figures of speech 'forget it' and 'forget about it', not statements such as "
                "'I forgot my keys', and not reminders ('don't forget to buy milk')."
            ),
            "recall": (
                "Asks what the assistant remembers or knows about the user, or whether it remembers a fact about "
                "the user, or to look up something previously saved."
            ),
            "restore": "Asks to bring back or undo the removal of something that was forgotten.",
            "none": (
                "Not about long-term memory: ordinary questions, chat, reminders for the user's own tasks, statements."
            ),
        },
    },
}


class UiAnswer(Contract):
    choice: str = Field(min_length=1, max_length=40)
    probability: float = Field(ge=0.0, le=1.0)


class UiClassification(Contract):
    """Validated answers to all six questions; immutable."""

    route: UiAnswer
    family: UiAnswer
    theme: UiAnswer
    captions: UiAnswer
    speed: UiAnswer
    voice: UiAnswer
    # Exposed for a follow-up that forces the main model's memory tools; never acted on here.
    memory_op: UiAnswer


class UiActionDecision(Contract):
    kind: Literal["action"] = "action"
    action: Action


class UiVoiceGenderDecision(Contract):
    kind: Literal["voice_gender"] = "voice_gender"
    gender: Literal["male", "female"]


class UiClarificationDecision(Contract):
    kind: Literal["clarification"] = "clarification"
    spoken: str = MULTIPLE_COMMANDS_SPOKEN


class UiWordleOpenDecision(Contract):
    """Open the daily Wordle: the worker runs the same server play as the play_wordle tool."""

    kind: Literal["wordle_open"] = "wordle_open"


UiDecision = UiActionDecision | UiVoiceGenderDecision | UiClarificationDecision | UiWordleOpenDecision


def build_ui_request(instruction: str) -> dict[str, Any]:
    """The single systemone request body; a fresh copy on every call."""
    questions = json.loads(json.dumps(UI_QUESTIONS))
    return {
        "model": JEV_MODEL,
        "state": {"current_instruction": instruction, "recent_context": [], "context_truncated": False},
        "questions": questions,
    }


def ui_text_is_eligible(text: str) -> bool:
    """Cheap pre-filter: long utterances are conversation, never commands."""
    stripped = text.strip()
    return bool(stripped) and len(stripped) <= MAX_UI_COMMAND_CHARS and len(stripped.split()) <= MAX_UI_COMMAND_WORDS


def parse_ui_response(body: object) -> UiClassification | None:
    """Validate a vendor payload; any deviation returns ``None``."""
    if not isinstance(body, Mapping) or body.get("model") != JEV_MODEL:
        return None
    answers = body.get("answers")
    if not isinstance(answers, Mapping):
        return None
    parsed: dict[str, UiAnswer] = {}
    for name, options in QUESTION_OPTIONS.items():
        answer = _parse_answer(answers.get(name), options)
        if answer is None:
            return None
        parsed[name] = answer
    return UiClassification(**parsed)


def _parse_answer(raw: object, options: tuple[str, ...]) -> UiAnswer | None:
    if not isinstance(raw, Mapping) or raw.get("type") != "choice":
        return None
    choice = raw.get("choice")
    if not isinstance(choice, str) or choice not in options:
        return None
    probabilities = raw.get("probabilities")
    value: object
    if isinstance(probabilities, Mapping):
        if any(key not in options for key in probabilities):
            return None
        value = probabilities.get(choice)
    else:
        value = raw.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        return None
    return UiAnswer(choice=choice, probability=float(value))


_THEMES: Final[dict[str, Literal["light", "dark", "system"]]] = {
    "light": "light",
    "dark": "dark",
    "system": "system",
}
_GENDERS: Final[dict[str, Literal["male", "female"]]] = {"male": "male", "female": "female"}


def _confident(answer: UiAnswer) -> bool:
    return answer.probability >= CONFIDENCE_THRESHOLD


def _detail_decision(family: str, detail: UiAnswer) -> UiDecision | None:
    if not _confident(detail):
        return None
    choice = detail.choice
    if family == "theme" and choice in _THEMES:
        return UiActionDecision(action=SetTheme(theme=_THEMES[choice]))
    if family == "captions" and choice in ("show", "hide"):
        return UiActionDecision(action=SetCaptionsVisible(visible=choice == "show"))
    if family == "speed" and choice in ("faster", "slower"):
        return UiActionDecision(action=AdjustSpeakingSpeed(delta=SPEED_STEP if choice == "faster" else -SPEED_STEP))
    if family == "voice" and choice in _GENDERS:
        return UiVoiceGenderDecision(gender=_GENDERS[choice])
    return None


def evaluate_ui_classification(result: UiClassification) -> UiDecision | None:
    """Walk the tree; ``None`` means fall through to the main model.

    Live evidence showed ``route`` is the weakest answer for polite requests
    ("Can you make it dark?"), so an action needs a confident family and detail
    that agree, and a route that is not a confident ``conversation``.  Only the
    compound clarification still requires a confident route.
    """
    route = result.route
    if route.choice == "multiple_commands":
        return UiClarificationDecision() if _confident(route) else None
    if route.choice == "conversation" and _confident(route):
        return None
    if not _confident(result.family):
        return None
    family = result.family.choice
    if family == "wordle":
        # No detail question: a confident family answer is the whole decision.
        return UiWordleOpenDecision()
    if family not in ("theme", "captions", "speed", "voice"):
        return None
    return _detail_decision(family, getattr(result, family))
