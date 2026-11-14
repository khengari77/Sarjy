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
    Action,
    AdjustSpeakingSpeed,
    Contract,
    OpenMemoryExplorer,
    SelectApprovedVoice,
    SetCaptionsVisible,
    ShowConversationHistory,
    UserSettings,
)

# §5.7 parity rows that the current typed actions genuinely support.  Two
# navigation spellings are accepted because the specification uses "Open my
# previous conversations" while the earlier typed command used "Show".
_EXACT_COMMANDS: dict[str, Action] = {
    "speak slower": AdjustSpeakingSpeed(delta=-0.1),
    "speak faster": AdjustSpeakingSpeed(delta=0.1),
    "hide captions": SetCaptionsVisible(visible=False),
    "show captions": SetCaptionsVisible(visible=True),
    "show my previous conversations": ShowConversationHistory(),
    "open my previous conversations": ShowConversationHistory(),
    "open the memory explorer": OpenMemoryExplorer(),
}

# "Use the British narrator voice."  The spoken name is resolved against the
# approved catalog; this parser never invents a voice id.
_VOICE_SELECTION = re.compile(
    r"^(?:use|switch to|change to|choose|select) (?:the )?(?P<name>[a-z0-9][a-z0-9 '\-]{0,98}?) voice$"
)

# Rows that are deliberately not wired yet.  Recognising them lets the product
# say so once instead of sending a destructive-sounding request to a
# conversational model that cannot perform it.
_UNSUPPORTED_COMMANDS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"^(?:show|find|search)(?: me)?(?: my| the)? memories(?: about .+)?$"),
        "MEMORY_SEARCH_UNSUPPORTED",
        "I can't search your memories by voice yet.",
    ),
    (
        re.compile(r"^(?:forget|exclude|remove|delete) .+$"),
        "MEMORY_MUTATION_UNSUPPORTED",
        "I can't change or delete memories by voice yet. Nothing was changed.",
    ),
    (
        re.compile(r"^restore (?:the |my )?(?:excluded )?memories\b.*$"),
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
            r"|(?:use|give) (?:shorter|longer) answers)\b.*$"
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
    changed: bool = False
    spoken: str = Field(min_length=1, max_length=300)
    detail: str | None = Field(default=None, max_length=100)


def parse_exact_command(text: str) -> Action | None:
    normalized = " ".join(text.strip().lower().split()).removesuffix(".")
    return _EXACT_COMMANDS.get(normalized)


def parse_voice_selection(text: str) -> str | None:
    """Return the spoken approved-voice name, or ``None``.

    The pattern requires a trailing "voice", so an ordinary sentence such as
    "use the kitchen" is not mistaken for a settings command.  Matching is
    case-insensitive while the returned name keeps the user's capitalisation
    for the truthful "not found" response.
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
    changed: bool = False,
    confirmed: bool | None = None,
    voice_name: str | None = None,
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
        changed=changed or navigation is not None,
        spoken=spoken_outcome(action, kind=kind, settings=settings, voice_name=voice_name),
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
    settings: UserSettings | None,
    voice_name: str | None = None,
) -> str:
    """One short, truthful sentence per handled command."""
    if isinstance(action, AdjustSpeakingSpeed):
        if kind is CommandKind.UNCHANGED:
            return "I can't speak any slower." if action.delta < 0 else "I can't speak any faster."
        return "Speaking slower." if action.delta < 0 else "Speaking faster."
    if isinstance(action, SetCaptionsVisible):
        if kind is CommandKind.UNCHANGED:
            return "Captions are already on." if action.visible else "Captions are already hidden."
        return "Captions shown." if action.visible else "Captions hidden."
    if isinstance(action, ShowConversationHistory):
        return "Opening your previous conversations."
    if isinstance(action, OpenMemoryExplorer):
        return "Opening the memory explorer."
    if isinstance(action, SelectApprovedVoice):
        label = voice_name or "the selected voice"
        if kind is CommandKind.UNCHANGED:
            return f"{label} is already selected."
        return f"Voice changed to {label}."
    return "Done."


def command_trace(result: CommandResult) -> str:
    """Stable identifier for parity assertions: the typed action, or the reason."""
    return result.action or result.detail or result.kind.value


__all__ = [
    "CommandKind",
    "CommandResult",
    "command_result",
    "command_trace",
    "failed_result",
    "parse_exact_command",
    "parse_voice_selection",
    "spoken_outcome",
    "unknown_result",
    "unsupported_command",
    "unsupported_result",
]
