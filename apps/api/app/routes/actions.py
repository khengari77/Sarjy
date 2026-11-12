"""UI and finalized-transcript adapters for the same deterministic dispatcher."""
from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.domain.commands import parse_exact_command
from app.domain.contracts import Action, UserSettings, parse_action
from app.infrastructure.database import UserSetting
from app.services.actions import dispatch_action, settings_value
from app.services.context_operations import ContextOperationError
from app.services.voices import VoiceCatalogError


class ActionSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    source: Literal['ui','transcript']
    action: Action | None = None
    transcript: str | None = Field(default=None, min_length=1, max_length=500)
    finalized: Literal[True] = True

    @field_validator('action', mode='before')
    @classmethod
    def validate_action(cls, value):
        if value is None:
            return None
        try:
            return parse_action(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(422, detail={'code': 'INVALID_ACTION'}) from exc

    @model_validator(mode='after')
    def one_input(self):
        if self.source == 'ui' and (self.action is None or self.transcript is not None):
            raise ValueError('UI_ACTION_REQUIRED')
        if self.source == 'transcript' and (self.transcript is None or self.action is not None):
            raise ValueError('FINAL_TRANSCRIPT_REQUIRED')
        return self


class SettingsActionResult(BaseModel):
    kind: Literal['AdjustSpeakingSpeed', 'SetSpeakingSpeed', 'SelectApprovedVoice', 'SetCaptionsVisible']
    settings: UserSettings
    changed: bool


class NavigationActionResult(BaseModel):
    kind: Literal['ShowConversationHistory', 'OpenMemoryExplorer']
    navigation: Literal['history', 'memory_explorer']


class HandledAction(BaseModel):
    handled: Literal[True]
    result: SettingsActionResult | NavigationActionResult | dict[str, JsonValue]


class UnhandledAction(BaseModel):
    handled: Literal[False]


ActionResponse = Annotated[HandledAction | UnhandledAction, Field(discriminator='handled')]


def register_action_routes(app, database, identity, owned, catalog):
    app.state.action_handlers = {}

    @app.get('/v1/settings', response_model=UserSettings)
    async def get_settings(user=Depends(identity), db=Depends(database)):
        return settings_value(await db.get(UserSetting,user.id)).model_dump(mode='json')

    @app.post('/v1/actions', response_model=ActionResponse)
    async def submit(body:ActionSubmission, user=Depends(identity), db=Depends(database)):
        await owned(body.conversation_id,user,db)
        if body.source == 'transcript':
            action = parse_exact_command(body.transcript)
            if action is None:
                return {'handled':False}
        else:
            action = body.action
        try:
            result = await dispatch_action(db,owner_id=user.id,conversation_id=body.conversation_id,
                request_id=body.request_id,expected_revision=body.expected_revision,action=action,
                catalog=catalog,handlers=app.state.action_handlers)
        except (VoiceCatalogError, ContextOperationError) as exc:
            raise HTTPException(exc.status,detail={'code':exc.code}) from exc
        return {'handled':True,'result':result}
