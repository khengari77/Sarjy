"""Transactional persistence for owner-keyed onboarding progress.

One row and one transaction per event.  The service never calls a model or
writes a memory row.  Voice and pace choices reuse the existing structured
settings store, so onboarding does not introduce a parallel preference store.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.contracts import SelectApprovedVoice, SetSpeakingSpeed, apply_settings_action
from app.domain.onboarding import (
    PACE_SPEEDS,
    AcknowledgeCapabilities,
    AcknowledgeVoice,
    ChoosePace,
    ChooseVoice,
    GreetingPlayed,
    OnboardingCapabilities,
    OnboardingError,
    OnboardingEvent,
    OnboardingProgress,
    OnboardingStage,
    Pace,
    Resume,
    Skip,
    Start,
    SubmitName,
    Suspend,
    initial_progress,
    reduce,
)
from app.infrastructure.database import OnboardingRecord, SettingRevision, UserSetting, now
from app.infrastructure.upsert import insert_ignore
from app.services.actions import settings_value


def _domain(row: OnboardingRecord) -> OnboardingProgress:
    return OnboardingProgress(
        stage=OnboardingStage(row.stage),
        schema_version=row.schema_version,
        revision=row.revision,
        preferred_name=row.preferred_name,
        selected_voice_id=row.selected_voice_id,
        selected_pace=Pace(row.selected_pace) if row.selected_pace else None,
        intro_played_at=row.intro_played_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        suspended_from=OnboardingStage(row.suspended_from) if row.suspended_from else None,
        last_event_id=row.last_event_id,
        applied_events=frozenset((str(event_id), str(payload)) for event_id, payload in row.applied_events),
    )


def _store(row: OnboardingRecord, progress: OnboardingProgress) -> None:
    row.schema_version = progress.schema_version
    row.stage = progress.stage.value
    row.revision = progress.revision
    row.preferred_name = progress.preferred_name
    row.selected_voice_id = progress.selected_voice_id
    row.selected_pace = progress.selected_pace.value if progress.selected_pace else None
    row.intro_played_at = progress.intro_played_at
    row.started_at = progress.started_at
    row.completed_at = progress.completed_at
    row.suspended_from = progress.suspended_from.value if progress.suspended_from else None
    row.last_event_id = progress.last_event_id
    row.applied_events = [list(entry) for entry in sorted(progress.applied_events)]
    row.updated_at = now()


def _values(owner_id: str, progress: OnboardingProgress) -> dict:
    return {
        "owner_id": owner_id,
        "schema_version": progress.schema_version,
        "stage": progress.stage.value,
        "revision": progress.revision,
        "preferred_name": progress.preferred_name,
        "selected_voice_id": progress.selected_voice_id,
        "selected_pace": progress.selected_pace.value if progress.selected_pace else None,
        "intro_played_at": progress.intro_played_at,
        "started_at": progress.started_at,
        "completed_at": progress.completed_at,
        "suspended_from": progress.suspended_from.value if progress.suspended_from else None,
        "last_event_id": progress.last_event_id,
        "applied_events": [list(entry) for entry in sorted(progress.applied_events)],
        "updated_at": now(),
    }


async def _create_once(db: AsyncSession, owner_id: str, progress: OnboardingProgress) -> OnboardingRecord:
    """Insert the first row atomically; concurrent creators keep the winner."""

    await insert_ignore(db, OnboardingRecord, _values(owner_id, progress), index_elements=["owner_id"])
    row = await db.get(OnboardingRecord, owner_id, with_for_update=True)
    if row is None:
        raise RuntimeError("ONBOARDING_RECORD_MISSING")
    return row


async def persist_setting(db: AsyncSession, owner_id: str, action: SelectApprovedVoice | SetSpeakingSpeed) -> None:
    row = await db.get(UserSetting, owner_id, with_for_update=True)
    before = settings_value(row)
    after = apply_settings_action(before, action)
    if after is before:
        return
    if row is None:
        row = UserSetting(owner_id=owner_id)
        db.add(row)
    for field in ("speaking_speed", "captions_visible", "approved_voice_id", "revision"):
        setattr(row, field, getattr(after, field))
    row.response_preference = after.response_preference
    row.updated_at = now()
    db.add(
        SettingRevision(
            id=str(uuid4()), owner_id=owner_id, revision=after.revision, settings=after.model_dump(mode="json")
        )
    )


def capabilities_for(config: Settings) -> OnboardingCapabilities:
    """The ElevenLabs runtime currently receives no speed parameter."""

    return OnboardingCapabilities(speaking_pace=config.speaking_pace_supported)


class OnboardingService:
    def __init__(self, db: AsyncSession, capabilities: OnboardingCapabilities):
        self.db = db
        self.capabilities = capabilities

    async def load(self, owner_id: str) -> OnboardingProgress:
        """Read progress, creating the first instance exactly once."""

        row = await self.db.get(OnboardingRecord, owner_id, with_for_update=True)
        if row is None:
            row = await _create_once(self.db, owner_id, initial_progress(now()))
        return _domain(row)

    async def apply(self, owner_id: str, event: OnboardingEvent) -> OnboardingProgress:
        at = now()
        row = await self.db.get(OnboardingRecord, owner_id, with_for_update=True)
        if row is None:
            row = await _create_once(self.db, owner_id, initial_progress(at))
        progress = _domain(row)
        updated = reduce(progress, event, at=at, capabilities=self.capabilities)
        if updated is progress:
            return progress
        _store(row, updated)
        if updated.selected_voice_id and updated.selected_voice_id != progress.selected_voice_id:
            await persist_setting(self.db, owner_id, SelectApprovedVoice(voice_id=updated.selected_voice_id))
        if updated.selected_pace is not None and updated.selected_pace != progress.selected_pace:
            await persist_setting(self.db, owner_id, SetSpeakingSpeed(speed=PACE_SPEEDS[updated.selected_pace]))
        return updated

    async def mark_greeting_played(self, owner_id: str) -> OnboardingProgress:
        """Idempotent worker callback: the real TTS greeting finished once."""

        progress = await self.load(owner_id)
        if progress.stage is not OnboardingStage.GREETING:
            return progress
        return await self.apply(owner_id, GreetingPlayed(event_id=f"worker-greeting:{owner_id}"))


def require_event(payload: dict[str, str | None]) -> OnboardingEvent:
    """Construct one typed event; payload shape is validated by the route."""

    kind = payload["type"]
    event_id = payload["event_id"]
    if kind == "start":
        return Start(event_id=event_id)
    if kind == "greeting_played":
        return GreetingPlayed(event_id=event_id)
    if kind == "submit_name":
        return SubmitName(event_id=event_id, name=payload["name"] or "")
    if kind == "choose_voice":
        return ChooseVoice(event_id=event_id, voice_id=payload.get("voice_id"))
    if kind == "acknowledge_voice":
        return AcknowledgeVoice(event_id=event_id)
    if kind == "choose_pace":
        return ChoosePace(event_id=event_id, pace=Pace(payload["pace"] or ""))
    if kind == "acknowledge_capabilities":
        return AcknowledgeCapabilities(event_id=event_id)
    if kind == "skip":
        return Skip(event_id=event_id)
    if kind == "suspend":
        return Suspend(event_id=event_id)
    if kind == "resume":
        return Resume(event_id=event_id)
    raise OnboardingError("ONBOARDING_EVENT_UNKNOWN")
