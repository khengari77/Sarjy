"""Pure Jev UI-command question wording, response validation and decision tree.

One request carries six small choice questions; code walks the answers.  An
action is produced only when the ``route`` answer, the ``family`` answer and the
family's detail answer are each at least :data:`CONFIDENCE_THRESHOLD` and agree.
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
MULTIPLE_COMMANDS_SPOKEN: Final = "I can only make one change at a time. Which one should I do first?"

_SCOPE = (
    "Judge only the current instruction itself. Negations ('don't switch to dark mode'), statements of "
    "preference ('I love dark mode'), quotations and questions about a feature are never commands."
)

QUESTION_OPTIONS: Final[dict[str, tuple[str, ...]]] = {
    "route": ("conversation", "ui_command", "multiple_commands"),
    "family": ("theme", "captions", "speed", "voice", "none"),
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
            "Is the current instruction a direct request to change an application setting right now? " + _SCOPE
        ),
        "criteria": {
            "conversation": (
                "Anything that is not a direct, present request to change a setting: questions, chat, "
                "statements, opinions, negated or quoted requests, questions about how a feature works."
            ),
            "ui_command": (
                "One direct request to change exactly one setting: the theme, caption visibility, speaking speed "
                "or the assistant's voice."
            ),
            "multiple_commands": "Direct requests to change two or more settings in the same instruction.",
        },
    },
    "family": {
        "type": "choice",
        "instructions": "Which setting does the current instruction directly ask to change? " + _SCOPE,
        "criteria": {
            "theme": "A direct request to change the appearance: light mode, dark mode or the system theme.",
            "captions": "A direct request to show or hide captions or subtitles.",
            "speed": "A direct request to make the assistant speak faster or slower.",
            "voice": "A direct request to change which voice the assistant speaks with.",
            "none": "No direct request to change any of these settings.",
        },
    },
    "theme": {
        "type": "choice",
        "instructions": "Which theme does the current instruction directly ask for? " + _SCOPE,
        "criteria": {
            "light": "A direct request to switch to light mode or a light theme.",
            "dark": "A direct request to switch to dark mode or a dark theme.",
            "system": "A direct request to follow the system or device theme.",
            "none": "No direct request to change the theme.",
        },
    },
    "captions": {
        "type": "choice",
        "instructions": "Does the current instruction directly ask to show or hide captions? " + _SCOPE,
        "criteria": {
            "show": "A direct request to turn captions or subtitles on or show them.",
            "hide": "A direct request to turn captions or subtitles off or hide them.",
            "none": "No direct request to change captions.",
        },
    },
    "speed": {
        "type": "choice",
        "instructions": "Does the current instruction directly ask the assistant to speak faster or slower? " + _SCOPE,
        "criteria": {
            "faster": "A direct request to speak faster, speed up or talk quicker.",
            "slower": "A direct request to speak slower, slow down or talk more slowly.",
            "none": "No direct request to change speaking speed.",
        },
    },
    "voice": {
        "type": "choice",
        "instructions": "Which voice does the current instruction directly ask for? " + _SCOPE,
        "criteria": {
            "male": "A direct request for a male voice.",
            "female": "A direct request for a female voice.",
            "named": "A direct request for a voice identified by a personal name.",
            "other": "A direct request for a voice defined in any other way, such as accent, age or tone.",
            "none": "No direct request to change the voice.",
        },
    },
    "memory_op": {
        "type": "choice",
        "instructions": (
            "Does the current instruction ask the assistant to save, forget, look up or restore something in its "
            "long-term memory about the user? Judge only the instruction itself."
        ),
        "criteria": {
            "remember": "Asks to remember, save, store or note a fact about the user for later.",
            "forget": (
                "Asks to forget, delete, remove, scrap, drop, erase or stop using something remembered about the user."
            ),
            "recall": (
                "Asks what the assistant remembers or knows about the user, or to look up something previously saved."
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


UiDecision = UiActionDecision | UiVoiceGenderDecision | UiClarificationDecision


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
    """Walk the tree; ``None`` means fall through to the main model."""
    if not _confident(result.route):
        return None
    if result.route.choice == "multiple_commands":
        return UiClarificationDecision()
    if result.route.choice != "ui_command" or not _confident(result.family):
        return None
    family = result.family.choice
    if family not in ("theme", "captions", "speed", "voice"):
        return None
    return _detail_decision(family, getattr(result, family))
