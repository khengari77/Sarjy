"""Authenticated, owner-keyed first-run onboarding endpoints.

The UI and the voice worker share one typed event reducer.  There is no consent
gate: memory transparency is informational and writes nothing.  Requests carry
client-generated event IDs so a retried tap or reconnect cannot double-save a
name, voice or pace preference.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.domain.onboarding import (
    OnboardingCapabilities,
    OnboardingError,
    OnboardingProgress,
    OnboardingStage,
    Pace,
    greeting_for,
)
from app.services.onboarding import OnboardingService, capabilities_for, require_event


class OnboardingEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=100)
    type: Literal[
        "start",
        "greeting_played",
        "submit_name",
        "choose_voice",
        "acknowledge_voice",
        "choose_pace",
        "acknowledge_capabilities",
        "skip",
        "suspend",
        "resume",
    ]
    name: str | None = Field(default=None, max_length=80)
    voice_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    pace: Pace | None = None

    @model_validator(mode="after")
    def payload_matches_type(self):
        payload_fields = {"name": self.name, "voice_id": self.voice_id, "pace": self.pace}
        if self.type == "submit_name":
            if self.name is None or self.voice_id is not None or self.pace is not None:
                raise ValueError("ONBOARDING_NAME_REQUIRED")
        elif self.type == "choose_pace":
            if self.pace is None or self.name is not None or self.voice_id is not None:
                raise ValueError("ONBOARDING_PACE_REQUIRED")
        elif self.type == "choose_voice":
            if self.name is not None or self.pace is not None:
                raise ValueError("ONBOARDING_VOICE_PAYLOAD_INVALID")
        elif any(value is not None for value in payload_fields.values()):
            raise ValueError("ONBOARDING_EVENT_PAYLOAD_INVALID")
        return self

    def payload(self) -> dict[str, str | None]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "name": self.name,
            "voice_id": self.voice_id,
            "pace": self.pace.value if self.pace else None,
        }


class OnboardingCapabilitiesResponse(BaseModel):
    speaking_pace: bool
    weather: bool = True
    memory: bool = True
    wordle: bool = True
    chat: bool = True


class OnboardingResponse(BaseModel):
    schema_version: int
    revision: int
    stage: OnboardingStage
    preferred_name: str | None = None
    selected_voice_id: str | None = None
    selected_pace: Pace | None = None
    intro_played_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    suspended_from: OnboardingStage | None = None
    last_event_id: str | None = None
    greeting: str | None = None
    voice_application: Literal["next_call"] | None = None
    capabilities: OnboardingCapabilitiesResponse


def _response(progress: OnboardingProgress, capabilities: OnboardingCapabilities) -> OnboardingResponse:
    return OnboardingResponse(
        schema_version=progress.schema_version,
        revision=progress.revision,
        stage=progress.stage,
        preferred_name=progress.preferred_name,
        selected_voice_id=progress.selected_voice_id,
        selected_pace=progress.selected_pace,
        intro_played_at=progress.intro_played_at,
        started_at=progress.started_at,
        completed_at=progress.completed_at,
        suspended_from=progress.suspended_from,
        last_event_id=progress.last_event_id,
        greeting=greeting_for(progress),
        voice_application="next_call" if progress.selected_voice_id else None,
        capabilities=OnboardingCapabilitiesResponse(speaking_pace=capabilities.speaking_pace),
    )


def onboarding_router(database, identity, config: Settings) -> APIRouter:
    router = APIRouter()
    capabilities = capabilities_for(config)

    @router.get("/v1/onboarding", response_model=OnboardingResponse)
    async def get_onboarding(user=Depends(identity), db=Depends(database)):
        progress = await OnboardingService(db, capabilities).load(user.id)
        await db.commit()
        return _response(progress, capabilities)

    @router.post("/v1/onboarding/events", response_model=OnboardingResponse)
    async def submit_event(body: OnboardingEventRequest, user=Depends(identity), db=Depends(database)):
        try:
            progress = await OnboardingService(db, capabilities).apply(user.id, require_event(body.payload()))
        except OnboardingError as exc:
            status = 422 if str(exc) == "ONBOARDING_NAME_INVALID" else 409
            raise HTTPException(status, detail={"code": str(exc)}) from exc
        await db.commit()
        return _response(progress, capabilities)

    return router
