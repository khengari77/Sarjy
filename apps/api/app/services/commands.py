"""One authoritative orchestration entry for a finalized user utterance.

The worker and the transcript adapter both call
:func:`handle_finalized_utterance`.  Interpretation is deterministic here (or
unsupported); the existing authorized dispatcher owns every effect and its
durable idempotency key.  No provider, generation or Jev call is made.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException

from app.domain.commands import (
    CommandKind,
    CommandResult,
    command_result,
    failed_result,
    parse_exact_command,
    parse_voice_selection,
    unknown_result,
    unsupported_command,
    unsupported_result,
)
from app.domain.contracts import (
    Action,
    SelectApprovedVoice,
    UserSettings,
)
from app.services.actions import SETTING_ACTIONS, dispatch_action
from app.services.context_operations import ContextOperationError
from app.services.voices import VoiceCatalogError


def command_request_id(turn_id: str) -> str:
    """Durable idempotency identity for one finalized source turn."""
    digest = hashlib.sha256(turn_id.encode()).hexdigest()[:32]
    return f"command:{digest}"


async def handle_finalized_utterance(
    db,
    *,
    owner_id: str,
    conversation_id: str,
    request_id: str,
    expected_revision: int,
    settings_revision: int,
    text: str,
    catalog,
    handlers,
) -> CommandResult | None:
    """Interpret and, when deterministic, execute exactly one action.

    ``None`` means "not a command": the caller keeps the guarded
    conversational path.  A returned result is already committed, or is an
    explicit unsupported/failed/unknown outcome.  One call can never apply more
    than one effect.
    """
    action = parse_exact_command(text)
    voice_name: str | None = None
    if action is None:
        voice_name = parse_voice_selection(text)
        if voice_name is not None:
            voice = await _resolve_voice(catalog, voice_name)
            if voice is None:
                return unsupported_result(
                    request_id=request_id,
                    detail="VOICE_NOT_FOUND",
                    spoken=f"I couldn't find an approved voice called {voice_name}.",
                )
            voice_name = voice.name
            action = SelectApprovedVoice(voice_id=voice.id)
    if action is None:
        unsupported = unsupported_command(text)
        if unsupported is not None:
            detail, spoken = unsupported
            return unsupported_result(request_id=request_id, detail=detail, spoken=spoken)
        return None

    revision = settings_revision if isinstance(action, SETTING_ACTIONS) else expected_revision
    try:
        raw = await dispatch_action(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            expected_revision=revision,
            action=action,
            catalog=catalog,
            handlers=handlers,
        )
    except (VoiceCatalogError, ContextOperationError) as exc:
        return failed_result(request_id=request_id, detail=exc.code, action=action)
    except HTTPException as exc:
        detail = _detail_code(exc)
        # A revision conflict is a safe, definite no-op; transport failures are
        # not proof either way.
        if exc.status_code == 409:
            return failed_result(request_id=request_id, detail=detail, action=action)
        return unknown_result(request_id=request_id, detail=detail, action=action)
    except Exception:
        return unknown_result(request_id=request_id, detail="ACTION_DELIVERY_UNKNOWN", action=action)
    return _describe(request_id=request_id, action=action, raw=raw, voice_name=voice_name)


async def _resolve_voice(catalog, name: str):
    find = getattr(catalog, "find_by_name", None)
    if find is None:
        return None
    try:
        return await find(name)
    except VoiceCatalogError:
        return None


def _describe(*, request_id: str, action: Action, raw: Any, voice_name: str | None) -> CommandResult:
    if not isinstance(raw, dict):
        return unknown_result(request_id=request_id, detail="ACTION_RESULT_INVALID", action=action)
    settings = raw.get("settings")
    if settings is not None:
        return command_result(
            request_id=request_id,
            action=action,
            settings=UserSettings.model_validate(settings),
            changed=bool(raw.get("changed", False)),
            voice_name=voice_name,
        )
    navigation = raw.get("navigation")
    if navigation in {"history", "memory_explorer"}:
        return command_result(request_id=request_id, action=action, navigation=navigation)
    confirmed = raw.get("confirmed")
    return command_result(
        request_id=request_id,
        action=action,
        confirmed=confirmed if isinstance(confirmed, bool) else None,
    )


def _detail_code(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str) and code:
            return code[:100]
    return f"HTTP_{exc.status_code}"


__all__ = [
    "CommandKind",
    "command_request_id",
    "handle_finalized_utterance",
]
