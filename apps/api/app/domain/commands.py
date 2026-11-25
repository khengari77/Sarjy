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

_THEME_NAMES: dict[str, ThemeName] = {
    "light": "light",
    "dark": "dark",
    "system": "system",
    "auto": "system",
    "automatic": "system",
}
_THEME_SETTING = re.compile(
    r"^(?:(?:switch|change|set|turn|go|flip|put)(?: it| the theme| the app| the display| the screen"
    r"| the interface| the ui)?(?: back)?(?: to| on)?|use|make it|enable|activate)"
    r" (?:the )?(?P<theme>light|dark|system|auto|automatic)(?: mode| theme)?$"
    r"|^(?P<bare>light|dark) mode$"
)
_SPEED_STEP = re.compile(
    r"^(?:speak|talk|go|read|reply|answer)(?: a (?:little )?bit| a little| a lot| much| even)?"
    r" (?P<direction>slower|faster|more slowly|more quickly)$"
)
_CAPTIONS_SETTING = re.compile(
    r"^(?:(?P<show>show|enable|turn on)|(?P<hide>hide|disable|turn off))(?: me)?(?: the)? (?:live )?(?:captions|subtitles)$"
    r"|^turn (?:the )?(?:captions|subtitles) (?:(?P<on>on)|(?P<off>off))$"
    r"|^(?:captions|subtitles) (?:(?P<on2>on)|(?P<off2>off))$"
)
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

# Voice memory exclusion is consent-first: only a simple "forget/exclude
# <target>" is understood here.  "delete" and "remove" keep their existing
# refusal because they carry a destructive, potentially global meaning.
_MEMORY_EXCLUDE = re.compile(r"^(?:please )?(?:forget|exclude) (?:about )?(?P<target>.+)$", re.IGNORECASE)
_MEMORY_GLOBAL = re.compile(
    r"\b(everywhere|global(?:ly)?|all (?:of )?(?:my )?(?:chats|conversations|memories)"
    r"|all (?:my )?chats|from every conversation|everything)\b"
)
# "forget the trip but keep the budget" is a compound request with its own
# keep-exception semantics; it stays unsupported rather than silently dropping
# the keep clause and excluding everything it matched.
# A keep clause is structural: after a comma or conjunction ("but I keep the budget"), or "keep the/my/it"
# directly after the target. "where I keep my passport" is a fact about the user, not a keep clause.
_KEEP_CLAUSE = (
    r"(?:(?:[,;]|\b(?:but|and|then|so)\b)\s*(?:(?:i|we|you|they)\s+)?keep\b"
    r"|(?<!\bi )(?<!\bwe )(?<!\byou )(?<!\bthey )(?<!\bwhere )(?<!\bwhat )(?<!\bto )(?<!\bcan )(?<!\bshould )(?<!\bwill )(?<!\bmust )\bkeep (?:the|my|it|that|everything)\b)"
)
_MEMORY_COMPOUND = re.compile(r"\b(?:but|and) (?:leave|except)\b|\bexcept\b|" + _KEEP_CLAUSE, re.IGNORECASE)
_MEMORY_RESTORE = re.compile(
    r"^(?:please )?(?:restore (?:the |my )?(?:excluded )?memories\b.*"
    r"|bring back (?:the |my )?memories\b.*"
    r"|undo (?:that |the )?(?:memory )?exclusion\b.*)$"
)
_MEMORY_UNDO = re.compile(
    r"^(?:please )?(?:undo(?: that| it| the hide)?|bring (?:it|that) back|unhide (?:it|that)|un-?hide)$"
)
_REFERENTIAL_TARGETS = frozenset({"it", "that", "this", "them", "those", "these", "everything", "anything", "all"})
# "forget it", "forget about it" and "forget you!" are figures of speech, not memory commands.
_FORGET_IDIOM_TARGETS = frozenset({"you", "me", "us", "him", "her", "yourself", "myself", "about it", "it all"})
# A target with a clause break ("the weather, tell me a joke") is a different request, not a memory name.
_CLAUSE_BREAK = re.compile(
    r"[,;:!?]"
    # Any period followed by a space ends the target, except after a known abbreviation or a single-letter
    # initial ("Dr. Smith's number", "the U.S. trip"). This is a deny-by-default sentence boundary, not a list
    # of clause openers: a missing opener must fail closed, never hide a memory the user said to keep.
    r"|(?<!\bdr)(?<!\bmr)(?<!\bmrs)(?<!\bms)(?<!\bst)(?<!\bjr)(?<!\bsr)(?<!\bprof)(?<!\bvs)(?<!\b[a-z])\.\s"
    r"|\b(?:and|then|so|because|but|while|(?:tell|give|show) me)\b",
    re.IGNORECASE,
)
_YES = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "ok",
        "okay",
        "sure",
        "confirm",
        "exclude it",
        "exclude that",
        "do it",
        "go ahead",
        "yes please",
    }
)
_NO = frozenset(
    {
        "no",
        "nope",
        "cancel",
        "discard it",
        "dont",
        "don't",
        "do not",
        "never mind",
        "nevermind",
        "no thanks",
        "stop",
    }
)

# "Change the voice to a man", "use a female voice": whole utterances only.
# Checked before the named-voice row so "a male" is never looked up as a name.
_GENDER_WORDS: dict[str, Literal["male", "female"]] = {
    "male": "male",
    "man": "male",
    "guy": "male",
    "gentleman": "male",
    "female": "female",
    "woman": "female",
    "lady": "female",
    "girl": "female",
}
_GENDER = "male|female|man|woman|guy|gentleman|lady|girl"
_VOICE_GENDER = re.compile(
    rf"^(?:(?:change|switch|use|give me|i want|i(?:'d| would) like|let me hear|speak in|talk in)(?: to)? (?:a |an |the )?"
    rf"(?P<a>{_GENDER})(?:'s)? voice"
    rf"|(?:change|switch|set) (?:the|my|your) voice to (?:a |an )?(?P<b>{_GENDER})(?:'s voice)?"
    rf"|(?:speak|talk|sound) like (?:a |an )?(?P<c>{_GENDER})"
    rf"|make (?:the|your) voice (?:a |an )?(?P<d>{_GENDER}))$"
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
            r"^(?:(?:remove|delete) .+"
            r"|(?:forget|exclude) .*(?:\b(?:but|and) (?:leave|except)\b|\bexcept\b|" + _KEEP_CLAUSE + r").*"
            r"|don't remember .+"
            r"|(?:delete|remove) (?:the )?memory of .+)$"
        ),
        "MEMORY_MUTATION_UNSUPPORTED",
        "I can't delete memories by voice. To delete one permanently, use the trash icon in the Memories "
        "panel, or say forget followed by what to hide. Nothing was changed.",
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
    # Opaque handle for the undo endpoint when a command applied a reversible
    # context change.  Existing UI normalisation ignores unknown fields.
    operation_id: str | None = Field(default=None, max_length=36)


_FILLER_LEAD = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|so|well|alright|um|uh|sarjy|and|then)\b[\s,.!]*)+", re.IGNORECASE
)
_POLITE_LEAD = re.compile(
    r"^(?:(?:can|could|would|will) you(?: (?:please|kindly))?(?: just)?|please|kindly"
    r"|i(?:'d| would) like you to|i want you to|i need you to|go ahead and|just)[\s,]+",
    re.IGNORECASE,
)
_POLITE_TAIL = re.compile(r"[\s,]+(?:please|for me|right now|now|thanks|thank you)$", re.IGNORECASE)


def normalize_utterance(text: str, *, fold: bool = True) -> str:
    """Strip case, trailing punctuation and conversational wrappers from a whole utterance.

    "Okay, can you change to light mode, please?" and "change to light mode" are the
    same request. Only wrappers around the whole utterance are removed, so a quoted,
    negated or compound request is still not a command.
    """
    value = " ".join(text.strip().split())
    if fold:
        value = value.lower()
    value = value.rstrip(" .!?")
    previous = None
    while previous != value:
        previous = value
        value = _FILLER_LEAD.sub("", value)
        value = _POLITE_LEAD.sub("", value)
        value = _POLITE_TAIL.sub("", value).rstrip(" .!?")
    return value


def parse_exact_command(text: str) -> Action | None:
    normalized = normalize_utterance(text)
    action = _EXACT_COMMANDS.get(normalized)
    if action is not None:
        return action
    theme = _THEME_SETTING.match(normalized)
    if theme is not None:
        return SetTheme(theme=_THEME_NAMES[theme.group("theme") or theme.group("bare")])
    captions = _CAPTIONS_SETTING.match(normalized)
    if captions is not None:
        shown = captions.group("show") or captions.group("on") or captions.group("on2")
        return SetCaptionsVisible(visible=bool(shown))
    if normalized in {"slow down", "speed up"}:
        return AdjustSpeakingSpeed(delta=-0.1 if normalized == "slow down" else 0.1)
    step = _SPEED_STEP.match(normalized)
    if step is not None:
        slower = step.group("direction") in {"slower", "more slowly"}
        return AdjustSpeakingSpeed(delta=-0.1 if slower else 0.1)
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
    normalized = normalize_utterance(text)
    match = _VOICE_GENDER.match(normalized)
    if match is None:
        return None
    return _GENDER_WORDS[match.group("a") or match.group("b") or match.group("c") or match.group("d")]


def parse_voice_selection(text: str) -> str | None:
    """Return the spoken approved-voice name, or ``None``.

    The pattern requires a trailing "voice", so "use the kitchen" is not a
    settings command. Matching is case-insensitive; the returned name keeps the
    user's capitalisation for an honest not-found response.
    """
    normalized = normalize_utterance(text, fold=False)
    match = _VOICE_SELECTION.match(normalized.casefold())
    if match is None:
        return None
    return " ".join(normalized[match.start("name") : match.end("name")].split())


def parse_memory_exclusion_target(text: str) -> str | None:
    """Return the spoken target of a simple "forget/exclude <target>" command.

    Compound, global and referential requests are deliberately not parsed here;
    the caller either refuses them honestly or leaves them to conversation.
    """
    normalized = normalize_utterance(text, fold=False)
    match = _MEMORY_EXCLUDE.match(normalized)
    if match is None:
        return None
    target = match.group("target").strip()
    if not target or target.casefold() in _REFERENTIAL_TARGETS | _FORGET_IDIOM_TARGETS:
        return None
    if _CLAUSE_BREAK.search(target):
        return None
    if _MEMORY_COMPOUND.search(normalized) or memory_request_is_global(normalized):
        return None
    return target


def memory_request_is_global(text: str) -> bool:
    """Whether a destructive memory request explicitly asks for every chat."""
    normalized = normalize_utterance(text)
    if not re.match(r"^(?:please )?(?:forget|exclude|delete|remove)\b", normalized):
        return False
    return bool(_MEMORY_GLOBAL.search(normalized))


def memory_restore_requested(text: str) -> bool:
    normalized = normalize_utterance(text)
    return bool(_MEMORY_RESTORE.match(normalized))


def memory_undo_requested(text: str) -> bool:
    """Bare "undo" / "bring it back": unhide the most recent hide."""
    return bool(_MEMORY_UNDO.match(normalize_utterance(text)))


def parse_exclusion_confirmation(text: str) -> bool | None:
    """Return the user's yes/no decision, or ``None`` for ordinary speech."""
    normalized = re.sub(r"[.,!?]+", "", " ".join(text.strip().casefold().split()))
    if normalized in _YES:
        return True
    if normalized in _NO:
        return False
    return None


def unsupported_command(text: str) -> tuple[str, str] | None:
    """Return ``(detail, spoken)`` for a known-but-unwired §5.7 row."""
    normalized = normalize_utterance(text)
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
    "memory_request_is_global",
    "memory_restore_requested",
    "parse_exact_command",
    "parse_exclusion_confirmation",
    "parse_memory_exclusion_target",
    "normalize_utterance",
    "parse_voice_selection",
    "spoken_outcome",
    "unknown_result",
    "unsupported_command",
    "unsupported_result",
]
