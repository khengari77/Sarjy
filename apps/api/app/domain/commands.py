"""Conservative whole-utterance command parsing and result contracts.

Pure domain: no I/O, no provider clients, no effects.  Only an exact, whole
utterance is a command.  A quoted, negated, compound or otherwise different
utterance is never parsed here; callers must fall through to the guarded
conversational path.
"""

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.domain.contracts import (
    SPEAKING_SPEED_MAX,
    SPEAKING_SPEED_MIN,
    Action,
    AdjustSpeakingSpeed,
    Contract,
    OpenMemoryExplorer,
    SelectApprovedVoice,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    SetTheme,
    ShowConversationHistory,
    ThemeName,
    UserSettings,
)

# §5.7 parity rows that the current typed actions genuinely support.  Two
# navigation spellings are accepted because the specification uses "Open my
# previous conversations" while the earlier typed command used "Show".
_EXACT_COMMANDS: dict[str, Action] = {
    "speak slower": AdjustSpeakingSpeed(delta=-0.1),
    "speak more slowly": AdjustSpeakingSpeed(delta=-0.1),
    "speak faster": AdjustSpeakingSpeed(delta=0.1),
    "speak more quickly": AdjustSpeakingSpeed(delta=0.1),
    "hide captions": SetCaptionsVisible(visible=False),
    "hide the captions": SetCaptionsVisible(visible=False),
    "turn off captions": SetCaptionsVisible(visible=False),
    "turn the captions off": SetCaptionsVisible(visible=False),
    "turn off the captions": SetCaptionsVisible(visible=False),
    "disable captions": SetCaptionsVisible(visible=False),
    "show captions": SetCaptionsVisible(visible=True),
    "show the captions": SetCaptionsVisible(visible=True),
    "turn on captions": SetCaptionsVisible(visible=True),
    "turn the captions on": SetCaptionsVisible(visible=True),
    "turn on the captions": SetCaptionsVisible(visible=True),
    "enable captions": SetCaptionsVisible(visible=True),
    "turn captions off": SetCaptionsVisible(visible=False),
    "turn captions on": SetCaptionsVisible(visible=True),
    "captions off": SetCaptionsVisible(visible=False),
    "captions on": SetCaptionsVisible(visible=True),
    "show my previous conversations": ShowConversationHistory(),
    "open my previous conversations": ShowConversationHistory(),
    "open the conversation history": ShowConversationHistory(),
    "show my conversations": ShowConversationHistory(),
    "open the memory explorer": OpenMemoryExplorer(),
    "show the memory explorer": OpenMemoryExplorer(),
    # Theme is browser-local and never a server preference. Every row is a
    # whole utterance, so "dark mode and hide captions" is not a command.
    "dark mode": SetTheme(theme="dark"),
    "light mode": SetTheme(theme="light"),
    "system theme": SetTheme(theme="system"),
    "follow the system theme": SetTheme(theme="system"),
    "use the system theme": SetTheme(theme="system"),
    "switch to dark mode": SetTheme(theme="dark"),
    "switch to light mode": SetTheme(theme="light"),
    "switch to dark theme": SetTheme(theme="dark"),
    "switch to light theme": SetTheme(theme="light"),
    "switch to the dark theme": SetTheme(theme="dark"),
    "switch to the light theme": SetTheme(theme="light"),
    "turn on dark mode": SetTheme(theme="dark"),
    "turn on light mode": SetTheme(theme="light"),
    "use dark mode": SetTheme(theme="dark"),
    "use light mode": SetTheme(theme="light"),
    "use dark theme": SetTheme(theme="dark"),
    "use light theme": SetTheme(theme="light"),
    "use the dark theme": SetTheme(theme="dark"),
    "use the light theme": SetTheme(theme="light"),
}

# "Set speed to 1.2", "set the speed to 0.8" and "speak at 1.5x" carry a value,
# so they are parsed instead of being dictionary rows. Out-of-range values are
# clamped to the provider's supported interval; a value off the 0.1 step is not
# a command.
_SPEED_SETTING = re.compile(
    r"^(?:set|change) (?:my |the )?speed to (?P<speed>\d{1,2}(?:\.\d)?)x?$"
    r"|^speak at (?P<rate>\d{1,2}(?:\.\d)?)x$"
)

# "Use the British female voice." is an argument-bearing row (PR 1b, behind
# An argument-bearing row. The spoken name is resolved against the approved
# catalog; this parser never invents a voice id.
_VOICE_SELECTION = re.compile(
    r"^(?:use|switch to|change to|choose|select) (?:the )?(?P<name>[a-z0-9][a-z0-9 '\-]{0,98}?) voice$"
)

# "Change the voice to a man", "use a female voice": whole utterances only.
# Checked before the named-voice row so "a male" is never looked up as a name.
_GENDER_WORDS: dict[str, Literal["male", "female"]] = {
    "male": "male",
    "man": "male",
    "female": "female",
    "woman": "female",
}
_VOICE_GENDER = re.compile(
    r"^(?:(?:change|switch|use)(?: to)? (?:a |an |the )?(?P<a>male|female|man|woman) voice"
    r"|change (?:the|my) voice to (?:a |an )?(?P<b>male|female|man|woman))$"
)

# Rows that are deliberately not wired yet.  Recognising them lets the product
# say so once instead of sending a destructive-sounding request to a
# conversational model that cannot perform it.
_UNSUPPORTED_COMMANDS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(
            r"^(?:(?:show|find|search)(?: me)?(?: my| the)? memories(?: about .+)?"
            r"|(?:what do you|do you) remember(?: about .+)?"
            r"|(?:look up|find|search)(?: my| the)? memories (?:about|for) .+)$"
        ),
        "MEMORY_SEARCH_UNSUPPORTED",
        "I can't search your memories by voice yet.",
    ),
    (
        re.compile(
            r"^(?:(?:forget|exclude|remove|delete) .+"
            r"|don't remember .+"
            r"|(?:delete|remove) (?:the )?memory of .+)$"
        ),
        "MEMORY_MUTATION_UNSUPPORTED",
        "I can't change or delete memories by voice yet. Nothing was changed.",
    ),
    (
        re.compile(
            r"^(?:restore (?:the |my )?(?:excluded )?memories\b.*"
            r"|bring back (?:the )?memories\b.*"
            r"|undo (?:that |the )?memory exclusion.*)$"
        ),
        "MEMORY_RESTORE_UNSUPPORTED",
        "I can't restore excluded memories by voice yet. Nothing was changed.",
    ),
    (
        re.compile(r"^clear (?:the )?(?:context|memory)$"),
        "MEMORY_SCOPE_REQUIRED",
        "Clearing context needs an explicit scope and is not available by voice yet. Nothing was changed.",
    ),
    (
        re.compile(
            r"^(?:give (?:me )?(?:shorter|longer|more detailed|more brief) answers"
            r"|(?:use|give) (?:shorter|longer) answers"
            r"|make (?:your )?answers (?:shorter|longer|more detailed|more brief)"
            r"|keep (?:your )?answers (?:short|brief)"
            r"|be (?:more )?(?:concise|brief))\b.*$"
        ),
        "RESPONSE_PREFERENCE_UNSUPPORTED",
        "I can't change answer length by voice yet.",
    ),
)


class CommandKind(StrEnum):
    EXECUTED = "executed"
    UNCHANGED = "unchanged"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    # A transport or delivery failure left the committed state genuinely
    # uncertain.  Never report success in this case.
    UNKNOWN = "unknown"


class CommandResult(Contract):
    """The single result the UI applies and the worker speaks once."""

    request_id: str = Field(min_length=1, max_length=100)
    kind: CommandKind
    action: str | None = Field(default=None, max_length=64)
    settings: UserSettings | None = None
    navigation: Literal["history", "memory_explorer"] | None = None
    theme: Literal["light", "dark", "system"] | None = None
    changed: bool = False
    spoken: str = Field(min_length=1, max_length=300)
    detail: str | None = Field(default=None, max_length=100)


def parse_exact_command(text: str) -> Action | None:
    normalized = " ".join(text.strip().lower().split()).removesuffix(".")
    action = _EXACT_COMMANDS.get(normalized)
    if action is not None:
        return action
    match = _SPEED_SETTING.match(normalized)
    if match is None:
        return None
    raw = match.group("speed") or match.group("rate")
    return SetSpeakingSpeed(speed=_clamp_speed(float(raw)))


def _clamp_speed(value: float) -> float:
    """Round to the 0.1 step and clamp into the interval the provider accepts."""
    stepped = round(value * 10) / 10
    return min(max(stepped, SPEAKING_SPEED_MIN), SPEAKING_SPEED_MAX)


def parse_voice_gender(text: str) -> Literal["male", "female"] | None:
    normalized = " ".join(text.strip().lower().split()).removesuffix(".")
    match = _VOICE_GENDER.match(normalized)
    if match is None:
        return None
    return _GENDER_WORDS[match.group("a") or match.group("b")]


def parse_voice_selection(text: str) -> str | None:
    """Return the spoken approved-voice name, or ``None``.

    The pattern requires a trailing "voice", so "use the kitchen" is not a
    settings command. Matching is case-insensitive; the returned name keeps the
    user's capitalisation for an honest not-found response.
    """
    normalized = " ".join(text.strip().split()).removesuffix(".")
    match = _VOICE_SELECTION.match(normalized.casefold())
    if match is None:
        return None
    return " ".join(normalized[match.start("name") : match.end("name")].split())


def unsupported_command(text: str) -> tuple[str, str] | None:
    """Return ``(detail, spoken)`` for a known-but-unwired §5.7 row."""
    normalized = " ".join(text.strip().lower().split()).removesuffix(".")
    for pattern, detail, spoken in _UNSUPPORTED_COMMANDS:
        if pattern.match(normalized):
            return detail, spoken
    return None


def command_result(
    *,
    request_id: str,
    action: Action,
    settings: UserSettings | None = None,
    navigation: Literal["history", "memory_explorer"] | None = None,
    theme: ThemeName | None = None,
    changed: bool = False,
    confirmed: bool | None = None,
    spoken: str | None = None,
) -> CommandResult:
    """Describe a committed dispatch outcome.  Never claims an uncommitted effect."""
    if settings is not None:
        kind = CommandKind.EXECUTED if changed else CommandKind.UNCHANGED
    elif confirmed is False:
        kind = CommandKind.NEEDS_CONFIRMATION
    else:
        kind = CommandKind.EXECUTED
    return CommandResult(
        request_id=request_id,
        kind=kind,
        action=action.kind,
        settings=settings,
        navigation=navigation,
        theme=theme,
        changed=changed or navigation is not None or theme is not None,
        spoken=spoken or spoken_outcome(action, kind=kind),
    )


def unsupported_result(*, request_id: str, detail: str, spoken: str) -> CommandResult:
    return CommandResult(
        request_id=request_id,
        kind=CommandKind.UNSUPPORTED,
        spoken=spoken,
        detail=detail,
    )


def failed_result(*, request_id: str, detail: str, action: Action | None = None) -> CommandResult:
    return CommandResult(
        request_id=request_id,
        kind=CommandKind.FAILED,
        action=action.kind if action is not None else None,
        spoken="I couldn't apply that. Nothing was changed.",
        detail=detail,
    )


def unknown_result(*, request_id: str, detail: str, action: Action | None = None) -> CommandResult:
    return CommandResult(
        request_id=request_id,
        kind=CommandKind.UNKNOWN,
        action=action.kind if action is not None else None,
        spoken="I'm not sure whether that was applied. Please check before trying again.",
        detail=detail,
    )


def spoken_outcome(
    action: Action,
    *,
    kind: CommandKind,
) -> str:
    """One short, truthful sentence per handled command."""
    if isinstance(action, AdjustSpeakingSpeed):
        if kind is CommandKind.UNCHANGED:
            return "I can't speak any slower." if action.delta < 0 else "I can't speak any faster."
        # The provider applies pace at the next turn's synthesis boundary, so the
        # sentence never claims the current sentence changed.
        return "Slower from my next reply." if action.delta < 0 else "Faster from my next reply."
    if isinstance(action, SetSpeakingSpeed):
        if kind is CommandKind.UNCHANGED:
            return f"Speaking speed is already {action.speed:.1f}."
        return f"Speaking speed set to {action.speed:.1f} for the next reply."
    if isinstance(action, SetCaptionsVisible):
        if kind is CommandKind.UNCHANGED:
            return "Captions are already on." if action.visible else "Captions are already hidden."
        return "Captions shown." if action.visible else "Captions hidden."
    if isinstance(action, SetTheme):
        return {
            "dark": "Dark mode on.",
            "light": "Light mode on.",
            "system": "Following the system theme.",
        }[action.theme]
    if isinstance(action, ShowConversationHistory):
        return "Opening your previous conversations."
    if isinstance(action, OpenMemoryExplorer):
        return "Opening the memory explorer."
    if isinstance(action, SelectApprovedVoice):
        return "Voice preference saved for the next call."
    return "Done."


__all__ = [
    "CommandKind",
    "CommandResult",
    "command_result",
    "failed_result",
    "parse_exact_command",
    "parse_voice_selection",
    "spoken_outcome",
    "unknown_result",
    "unsupported_command",
    "unsupported_result",
]
