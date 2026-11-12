"""UI and finalized-transcript adapters for the same deterministic dispatcher."""
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.commands import parse_exact_command
from app.domain.contracts import parse_action
from app.infrastructure.database import UserSetting
from app.services.actions import dispatch_action, settings_value
from app.services.voices import VoiceCatalogError


class ActionSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    source: Literal['ui','transcript']
    action: dict | None = None
    transcript: str | None = Field(default=None, min_length=1, max_length=500)
    finalized: Literal[True] = True

    @model_validator(mode='after')
    def one_input(self):
        if self.source == 'ui' and (self.action is None or self.transcript is not None):
            raise ValueError('UI_ACTION_REQUIRED')
        if self.source == 'transcript' and (self.transcript is None or self.action is not None):
            raise ValueError('FINAL_TRANSCRIPT_REQUIRED')
        return self


def register_action_routes(app, database, identity, owned, catalog):
    app.state.action_handlers = {}

    @app.get('/v1/settings')
    async def get_settings(user=Depends(identity), db=Depends(database)):
        return settings_value(await db.get(UserSetting,user.id)).model_dump(mode='json')

    @app.post('/v1/actions')
    async def submit(body:ActionSubmission, user=Depends(identity), db=Depends(database)):
        await owned(body.conversation_id,user,db)
        if body.source == 'transcript':
            action = parse_exact_command(body.transcript)
            if action is None:
                return {'handled':False}
        else:
            from pydantic import ValidationError
            try:
                action = parse_action(body.action)
            except (ValidationError, ValueError, TypeError, OverflowError) as exc:
                raise HTTPException(422,detail={'code':'INVALID_ACTION'}) from exc
        try:
            result = await dispatch_action(db,owner_id=user.id,conversation_id=body.conversation_id,
                request_id=body.request_id,expected_revision=body.expected_revision,action=action,
                catalog=catalog,handlers=app.state.action_handlers)
        except VoiceCatalogError as exc:
            raise HTTPException(exc.status,detail={'code':exc.code}) from exc
        return {'handled':True,'result':result}
