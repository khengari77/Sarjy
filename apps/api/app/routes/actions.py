"""UI and finalized-transcript adapters for the same deterministic dispatcher."""

from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from sqlalchemy import select

from app.domain.commands import parse_exact_command
from app.domain.contracts import Action, UserSettings, parse_action
from app.infrastructure.database import UserSetting, VoiceControl, VoiceSession
from app.services.actions import dispatch_action, settings_value
from app.services.context_operations import ContextOperationError
from app.services.voice_control import VoiceControlError, deliver_control, latest_control, voice_state
from app.services.voices import VoiceCatalogError


class ActionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    source: Literal["ui", "transcript"]
    action: Action | None = None
    transcript: str | None = Field(default=None, min_length=1, max_length=500)
    finalized: Literal[True] = True

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, value):
        if value is None:
            return None
        try:
            return parse_action(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(422, detail={"code": "INVALID_ACTION"}) from exc

    @model_validator(mode="after")
    def one_input(self):
        if self.source == "ui" and (self.action is None or self.transcript is not None):
            raise ValueError("UI_ACTION_REQUIRED")
        if self.source == "transcript" and (self.transcript is None or self.action is not None):
            raise ValueError("FINAL_TRANSCRIPT_REQUIRED")
        return self


class VoiceControlState(BaseModel):
    session_id: str
    conversation_id: str
    active_voice_id: str | None
    active_settings_revision: int
    request_id: str | None
    voice_id: str | None
    settings_revision: int | None
    status: Literal["idle", "pending", "dispatched", "applied", "failed", "superseded"]
    error_code: str | None


class SettingsActionResult(BaseModel):
    kind: Literal["AdjustSpeakingSpeed", "SetSpeakingSpeed", "SelectApprovedVoice", "SetCaptionsVisible"]
    settings: UserSettings
    changed: bool
    voice_control: VoiceControlState | None = None


class NavigationActionResult(BaseModel):
    kind: Literal["ShowConversationHistory", "OpenMemoryExplorer"]
    navigation: Literal["history", "memory_explorer"]


class HandledAction(BaseModel):
    handled: Literal[True]
    result: SettingsActionResult | NavigationActionResult | dict[str, JsonValue]


class UnhandledAction(BaseModel):
    handled: Literal[False]


ActionResponse = Annotated[HandledAction | UnhandledAction, Field(discriminator="handled")]


def register_action_routes(app, database, identity, owned, catalog, *, config, delivery):
    app.state.action_handlers = {}

    @app.get("/v1/settings", response_model=UserSettings)
    async def get_settings(user=Depends(identity), db=Depends(database)):
        return settings_value(await db.get(UserSetting, user.id)).model_dump(mode="json")

    @app.post("/v1/actions", response_model=ActionResponse)
    async def submit(body: ActionSubmission, user=Depends(identity), db=Depends(database)):
        await owned(body.conversation_id, user, db)
        if body.source == "transcript":
            action = parse_exact_command(body.transcript)
            if action is None:
                return {"handled": False}
        else:
            action = body.action
        try:
            result = await dispatch_action(
                db,
                owner_id=user.id,
                conversation_id=body.conversation_id,
                request_id=body.request_id,
                expected_revision=body.expected_revision,
                action=action,
                catalog=catalog,
                handlers=app.state.action_handlers,
            )
        except (VoiceCatalogError, ContextOperationError) as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from exc
        state = result.get("voice_control") if isinstance(result, dict) else None
        if isinstance(state, dict):
            result = {
                **result,
                "voice_control": await _deliver_authorized_control(
                    db, delivery=delivery, secret=config.agent_service_secret, state=state
                ),
            }
        return {"handled": True, "result": result}

    @app.get("/v1/voice/sessions/{session_id}/voice", response_model=VoiceControlState)
    async def voice_control_state(session_id: str, user=Depends(identity), db=Depends(database)):
        voice = await db.get(VoiceSession, session_id)
        if voice is None or voice.owner_id != user.id:
            raise HTTPException(404, detail={"code": "VOICE_SESSION_NOT_FOUND"})
        return voice_state(voice, await latest_control(db, voice.id))


async def _deliver_authorized_control(db, *, delivery, secret: str, state: dict) -> dict:
    control = await db.scalar(
        select(VoiceControl).where(
            VoiceControl.session_id == state["session_id"],
            VoiceControl.request_id == state["request_id"],
        )
    )
    if control is None:
        return state
    try:
        return await deliver_control(db, control_id=control.id, delivery=delivery, secret=secret)
    except VoiceControlError as exc:
        return {**state, "status": "failed", "error_code": exc.code}
