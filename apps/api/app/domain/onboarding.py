"""Deterministic first-run onboarding state machine.

The reducer is pure: it takes the last persisted progress, one typed event and
the server's capability snapshot, and returns either the same immutable value
(for a replay or a terminal no-op) or a new value.  Persistence and provider
effects live in :mod:`app.services.onboarding`.

Memory is transparent by default in Sarjy's owner decision.  Onboarding has no
consent stage and writes no memory rows; the capability intro only links to the
existing explorer.
"""

from dataclasses import dataclass, field, replace
from enum import StrEnum

SCHEMA_VERSION = 1
MAX_NAME_LENGTH = 80
GREETING = "Hey, I'm Sarjy. What should I call you?"


class OnboardingStage(StrEnum):
    NOT_STARTED = "not_started"
    GREETING = "greeting"
    PREFERRED_NAME = "preferred_name"
    VOICE_CHOICE = "voice_choice"
    AWAITING_VOICE_ACK = "awaiting_voice_ack"
    SPEAKING_PACE = "speaking_pace"
    CAPABILITY_INTRO = "capability_intro"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    SUSPENDED = "suspended"


ACTIVE_STAGES = frozenset(
    {
        OnboardingStage.GREETING,
        OnboardingStage.PREFERRED_NAME,
        OnboardingStage.VOICE_CHOICE,
        OnboardingStage.AWAITING_VOICE_ACK,
        OnboardingStage.SPEAKING_PACE,
        OnboardingStage.CAPABILITY_INTRO,
    }
)
TERMINAL_STAGES = frozenset({OnboardingStage.COMPLETED, OnboardingStage.SKIPPED})


class Pace(StrEnum):
    SLOWER = "slower"
    NORMAL = "normal"
    FASTER = "faster"


PACE_SPEEDS: dict[Pace, float] = {Pace.SLOWER: 0.9, Pace.NORMAL: 1.0, Pace.FASTER: 1.1}


@dataclass(frozen=True)
class OnboardingCapabilities:
    """Only advertise behaviour the runtime can actually honour."""

    speaking_pace: bool = False


@dataclass(frozen=True)
class OnboardingProgress:
    stage: OnboardingStage = OnboardingStage.NOT_STARTED
    schema_version: int = SCHEMA_VERSION
    revision: int = 0
    preferred_name: str | None = None
    selected_voice_id: str | None = None
    selected_pace: Pace | None = None
    intro_played_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    suspended_from: OnboardingStage | None = None
    last_event_id: str | None = None
    # Bounded by the handful of onboarding events; used for idempotent replay.
    applied_events: frozenset[tuple[str, str]] = field(default_factory=frozenset)


class OnboardingError(ValueError):
    """A deterministic refusal that the HTTP adapter maps to 409/422."""


@dataclass(frozen=True)
class Start:
    event_id: str
    kind: str = "start"

    def payload(self) -> str:
        return self.kind


@dataclass(frozen=True)
class GreetingPlayed:
    event_id: str
    kind: str = "greeting_played"

    def payload(self) -> str:
        return self.kind


@dataclass(frozen=True)
class SubmitName:
    event_id: str
    name: str
    kind: str = "submit_name"

    def payload(self) -> str:
        return f"{self.kind}:{self.name.strip()}"


@dataclass(frozen=True)
class ChooseVoice:
    event_id: str
    voice_id: str | None = None
    kind: str = "choose_voice"

    def payload(self) -> str:
        return f"{self.kind}:{self.voice_id or ''}"


@dataclass(frozen=True)
class AcknowledgeVoice:
    event_id: str
    kind: str = "acknowledge_voice"

    def payload(self) -> str:
        return self.kind


@dataclass(frozen=True)
class ChoosePace:
    event_id: str
    pace: Pace
    kind: str = "choose_pace"

    def payload(self) -> str:
        return f"{self.kind}:{self.pace.value}"


@dataclass(frozen=True)
class AcknowledgeCapabilities:
    event_id: str
    kind: str = "acknowledge_capabilities"

    def payload(self) -> str:
        return self.kind


@dataclass(frozen=True)
class Skip:
    event_id: str
    kind: str = "skip"

    def payload(self) -> str:
        return self.kind


@dataclass(frozen=True)
class Suspend:
    event_id: str
    kind: str = "suspend"

    def payload(self) -> str:
        return self.kind


@dataclass(frozen=True)
class Resume:
    event_id: str
    kind: str = "resume"

    def payload(self) -> str:
        return self.kind


OnboardingEvent = (
    Start
    | GreetingPlayed
    | SubmitName
    | ChooseVoice
    | AcknowledgeVoice
    | ChoosePace
    | AcknowledgeCapabilities
    | Skip
    | Suspend
    | Resume
)


def initial_progress(at: str) -> OnboardingProgress:
    """A brand-new onboarding instance already shows its greeting."""

    return OnboardingProgress(stage=OnboardingStage.GREETING, started_at=at, revision=1)


def greeting_for(progress: OnboardingProgress | None) -> str | None:
    """Spoken/displayed greeting, only while it has not been played yet."""

    if progress is None:
        return GREETING
    if progress.stage is OnboardingStage.GREETING and progress.intro_played_at is None:
        return GREETING
    return None


def _next_after_voice(capabilities: OnboardingCapabilities) -> OnboardingStage:
    return OnboardingStage.SPEAKING_PACE if capabilities.speaking_pace else OnboardingStage.CAPABILITY_INTRO


def _resumable(progress: OnboardingProgress) -> OnboardingStage:
    stage = progress.suspended_from or OnboardingStage.CAPABILITY_INTRO
    if stage is OnboardingStage.GREETING and progress.intro_played_at is not None:
        return OnboardingStage.PREFERRED_NAME
    return stage


def _require(condition: bool) -> None:
    if not condition:
        raise OnboardingError("ONBOARDING_TRANSITION_INVALID")


def _apply_basics(progress: OnboardingProgress, event: OnboardingEvent, at: str) -> OnboardingProgress:
    stage = progress.stage
    if isinstance(event, Start):
        if stage is not OnboardingStage.NOT_STARTED:
            return progress
        return replace(progress, stage=OnboardingStage.GREETING, started_at=at)
    if isinstance(event, GreetingPlayed):
        if stage is OnboardingStage.PREFERRED_NAME:
            return progress
        _require(stage is OnboardingStage.GREETING)
        return replace(progress, stage=OnboardingStage.PREFERRED_NAME, intro_played_at=progress.intro_played_at or at)
    if isinstance(event, SubmitName):
        _require(stage is OnboardingStage.PREFERRED_NAME)
        name = " ".join(event.name.split())
        if not name or len(name) > MAX_NAME_LENGTH:
            raise OnboardingError("ONBOARDING_NAME_INVALID")
        return replace(progress, stage=OnboardingStage.VOICE_CHOICE, preferred_name=name)
    if isinstance(event, AcknowledgeCapabilities):
        _require(stage is OnboardingStage.CAPABILITY_INTRO)
        return replace(progress, stage=OnboardingStage.COMPLETED, completed_at=at)
    raise OnboardingError("ONBOARDING_EVENT_UNKNOWN")


def _apply_preference(
    progress: OnboardingProgress, event: OnboardingEvent, capabilities: OnboardingCapabilities
) -> OnboardingProgress:
    stage = progress.stage
    if isinstance(event, ChooseVoice):
        _require(stage is OnboardingStage.VOICE_CHOICE)
        return replace(
            progress,
            stage=_next_after_voice(capabilities),
            selected_voice_id=event.voice_id or progress.selected_voice_id,
        )
    if isinstance(event, AcknowledgeVoice):
        _require(stage is OnboardingStage.AWAITING_VOICE_ACK)
        return replace(progress, stage=_next_after_voice(capabilities))
    if isinstance(event, ChoosePace):
        _require(stage is OnboardingStage.SPEAKING_PACE and capabilities.speaking_pace)
        return replace(progress, stage=OnboardingStage.CAPABILITY_INTRO, selected_pace=event.pace)
    raise OnboardingError("ONBOARDING_EVENT_UNKNOWN")


def _apply_interruption(progress: OnboardingProgress, event: OnboardingEvent) -> OnboardingProgress:
    stage = progress.stage
    if isinstance(event, Skip):
        _require(stage in ACTIVE_STAGES or stage is OnboardingStage.SUSPENDED)
        return replace(progress, stage=OnboardingStage.SKIPPED, suspended_from=None)
    if isinstance(event, Suspend):
        _require(stage in ACTIVE_STAGES)
        return replace(progress, stage=OnboardingStage.SUSPENDED, suspended_from=stage)
    if isinstance(event, Resume):
        _require(stage is OnboardingStage.SUSPENDED)
        return replace(progress, stage=_resumable(progress), suspended_from=None)
    raise OnboardingError("ONBOARDING_EVENT_UNKNOWN")


def _apply(
    progress: OnboardingProgress,
    event: OnboardingEvent,
    *,
    at: str,
    capabilities: OnboardingCapabilities,
) -> OnboardingProgress:
    if isinstance(event, (Start, GreetingPlayed, SubmitName, AcknowledgeCapabilities)):
        return _apply_basics(progress, event, at)
    if isinstance(event, (ChooseVoice, AcknowledgeVoice, ChoosePace)):
        return _apply_preference(progress, event, capabilities)
    return _apply_interruption(progress, event)


def reduce(
    progress: OnboardingProgress,
    event: OnboardingEvent,
    *,
    at: str,
    capabilities: OnboardingCapabilities = OnboardingCapabilities(),
) -> OnboardingProgress:
    """Apply one event exactly once. Replays and terminal states are no-ops."""

    previous = dict(progress.applied_events).get(event.event_id)
    if previous is not None:
        if previous != event.payload():
            raise OnboardingError("ONBOARDING_EVENT_CONFLICT")
        return progress
    if progress.stage in TERMINAL_STAGES:
        return progress
    updated = _apply(progress, event, at=at, capabilities=capabilities)
    if updated is progress:
        return progress
    return replace(
        updated,
        revision=progress.revision + 1,
        last_event_id=event.event_id,
        applied_events=progress.applied_events | {(event.event_id, event.payload())},
    )
