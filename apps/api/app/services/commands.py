"""One authoritative orchestration entry for a finalized user utterance.

The worker and the transcript adapter both call
:func:`handle_finalized_utterance`.  Interpretation is deterministic here (or
unsupported).  The existing authorized dispatcher/settings writer owns every
effect and its durable idempotency key.  No provider or generation call is made.
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
    memory_request_is_global,
    memory_restore_requested,
    parse_exact_command,
    parse_exclusion_confirmation,
    parse_memory_exclusion_target,
    parse_voice_selection,
    unknown_result,
    unsupported_command,
    unsupported_result,
)
from app.domain.contracts import (
    Action,
    AdjustSpeakingSpeed,
    SelectApprovedVoice,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    UserSettings,
)
from app.services.actions import dispatch_action, dispatch_setting
from app.services.context_operations import ContextOperationError, MemorySearcher, keyword_search
from app.services.voice_exclusions import (
    discard_pending,
    global_retraction_refusal,
    propose_exclusion,
    propose_restore,
    resolve_confirmation,
)
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
    approved_voice_ids: frozenset[str] | None = None,
    handlers,
    secret: str = "",
    search: MemorySearcher = keyword_search,
) -> CommandResult | None:
    """Interpret and, when deterministic, execute exactly one action.

    ``None`` means "not a command": the caller keeps the guarded
    conversational path.  A returned result describes a committed effect or an
    explicit unsupported/failed/unknown outcome.  One call never applies more
    than one effect.
    """
    memory_result = await _handle_memory_confirmation(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        text=text,
        secret=secret,
    )
    if memory_result is None:
        memory_result = await _handle_memory_mutation(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            text=text,
            secret=secret,
            search=search,
        )
    if memory_result is not None:
        return memory_result

    action = parse_exact_command(text)
    voice_name: str | None = None
    if action is None:
        voice_name = parse_voice_selection(text)
        if voice_name is not None:
            resolved = await _resolve_voice(catalog, voice_name)
            if resolved is None:
                return unsupported_result(
                    request_id=request_id,
                    detail="VOICE_NOT_FOUND",
                    spoken=f"I couldn't find an approved voice called {voice_name}.",
                )
            if approved_voice_ids is not None and resolved.id not in approved_voice_ids:
                return unsupported_result(
                    request_id=request_id,
                    detail="VOICE_NOT_APPROVED",
                    spoken=f"The voice called {resolved.name} isn't approved.",
                )
            voice_name = resolved.name
            action = SelectApprovedVoice(voice_id=resolved.id)
    if action is None:
        return await _unsupported_result(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            text=text,
        )

    # Truthful routing for an effect that is not wired end-to-end yet. Speed is
    # stored but never reaches the TTS voice settings (#44), so no success is
    # claimed and nothing is mutated.
    if isinstance(action, (AdjustSpeakingSpeed, SetSpeakingSpeed)):
        return unsupported_result(
            request_id=request_id,
            detail="SPEED_APPLICATION_UNAVAILABLE",
            spoken="I can't change speaking speed in the call yet. Nothing was changed.",
        )

    # Captions and voice are owner-scoped preferences: exactly one writer,
    # POST /v1/settings/actions, never the conversation-scoped dispatcher.
    if isinstance(action, SetCaptionsVisible):
        setting_request = f"captions:{settings_revision}:{'on' if action.visible else 'off'}"
        return await _run_setting(
            db,
            owner_id=owner_id,
            request_id=setting_request,
            settings_revision=settings_revision,
            action=action,
            catalog=catalog,
            approved_voice_ids=approved_voice_ids,
            spoken=None,
        )
    if isinstance(action, SelectApprovedVoice):
        setting_request = f"voice:{settings_revision}:{action.voice_id or 'default'}"
        return await _run_setting(
            db,
            owner_id=owner_id,
            request_id=setting_request,
            settings_revision=settings_revision,
            action=action,
            catalog=catalog,
            approved_voice_ids=approved_voice_ids,
            spoken=f"Voice set to {voice_name} for the next call.",
        )

    return await _run_navigation(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        expected_revision=expected_revision,
        action=action,
        catalog=catalog,
        handlers=handlers,
    )


async def _unsupported_result(
    db, *, owner_id: str, conversation_id: str, request_id: str, text: str
) -> CommandResult | None:
    unsupported = unsupported_command(text)
    if unsupported is None:
        return None
    detail, spoken = unsupported
    if detail.startswith("MEMORY_"):
        await discard_pending(db, owner_id=owner_id, conversation_id=conversation_id)
    return unsupported_result(request_id=request_id, detail=detail, spoken=spoken)


async def _handle_memory_confirmation(
    db, *, owner_id: str, conversation_id: str, request_id: str, text: str, secret: str
) -> CommandResult | None:
    confirmation = parse_exclusion_confirmation(text)
    if confirmation is None:
        return None
    try:
        return await resolve_confirmation(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            confirmed=confirmation,
            request_id=request_id,
            secret=secret,
        )
    except ContextOperationError as exc:
        return failed_result(request_id=request_id, detail=exc.code)
    except Exception:
        return unknown_result(request_id=request_id, detail="MEMORY_DECISION_DELIVERY_UNKNOWN")


async def _handle_memory_mutation(
    db, *, owner_id: str, conversation_id: str, request_id: str, text: str, secret: str, search: MemorySearcher
) -> CommandResult | None:
    if memory_request_is_global(text):
        await discard_pending(db, owner_id=owner_id, conversation_id=conversation_id)
        return global_retraction_refusal(request_id)
    if memory_restore_requested(text):
        return await _run_memory_command(
            propose_restore(
                db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                request_id=request_id,
                secret=secret,
            ),
            request_id=request_id,
        )
    target = parse_memory_exclusion_target(text)
    if target is None:
        return None
    return await _run_memory_command(
        propose_exclusion(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            target=target,
            request_id=request_id,
            secret=secret,
            search=search,
        ),
        request_id=request_id,
    )


async def _run_memory_command(operation, *, request_id: str) -> CommandResult:
    try:
        return await operation
    except ContextOperationError as exc:
        return failed_result(request_id=request_id, detail=exc.code)
    except Exception:
        return unknown_result(request_id=request_id, detail="MEMORY_COMMAND_DELIVERY_UNKNOWN")


async def _run_setting(
    db,
    *,
    owner_id: str,
    request_id: str,
    settings_revision: int,
    action: Action,
    catalog,
    approved_voice_ids: frozenset[str] | None,
    spoken: str | None,
) -> CommandResult:
    try:
        raw = await dispatch_setting(
            db,
            owner_id=owner_id,
            request_id=request_id,
            expected_revision=settings_revision,
            action=action,
            catalog=catalog,
            approved_voice_ids=approved_voice_ids,
        )
    except (VoiceCatalogError, ContextOperationError) as exc:
        return failed_result(request_id=request_id, detail=exc.code, action=action)
    except HTTPException as exc:
        return _http_failure(request_id=request_id, action=action, exc=exc)
    except Exception:
        return unknown_result(request_id=request_id, detail="SETTING_DELIVERY_UNKNOWN", action=action)
    return _describe(request_id=request_id, action=action, raw=raw, spoken=spoken)


async def _run_navigation(
    db,
    *,
    owner_id: str,
    conversation_id: str,
    request_id: str,
    expected_revision: int,
    action: Action,
    catalog,
    handlers,
) -> CommandResult:
    try:
        raw = await dispatch_action(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            expected_revision=expected_revision,
            action=action,
            catalog=catalog,
            handlers=handlers,
        )
    except (VoiceCatalogError, ContextOperationError) as exc:
        return failed_result(request_id=request_id, detail=exc.code, action=action)
    except HTTPException as exc:
        return _http_failure(request_id=request_id, action=action, exc=exc)
    except Exception:
        return unknown_result(request_id=request_id, detail="ACTION_DELIVERY_UNKNOWN", action=action)
    return _describe(request_id=request_id, action=action, raw=raw, spoken=None)


def _http_failure(*, request_id: str, action: Action, exc: HTTPException) -> CommandResult:
    detail = _detail_code(exc)
    # A rejected request is a definite no-op. A 5xx or transport failure is not
    # proof either way and must never be reported as success.
    if exc.status_code < 500:
        return failed_result(request_id=request_id, detail=detail, action=action)
    return unknown_result(request_id=request_id, detail=detail, action=action)


async def _resolve_voice(catalog, name: str):
    find = getattr(catalog, "find_by_name", None)
    if find is None:
        return None
    try:
        return await find(name)
    except VoiceCatalogError:
        return None


def _describe(*, request_id: str, action: Action, raw: Any, spoken: str | None) -> CommandResult:
    if not isinstance(raw, dict):
        return unknown_result(request_id=request_id, detail="ACTION_RESULT_INVALID", action=action)
    settings = raw.get("settings")
    if settings is not None:
        return command_result(
            request_id=request_id,
            action=action,
            settings=UserSettings.model_validate(settings),
            changed=bool(raw.get("changed", False)),
            spoken=spoken,
        )
    navigation = raw.get("navigation")
    if navigation in {"history", "memory_explorer"}:
        return command_result(request_id=request_id, action=action, navigation=navigation)
    confirmed = raw.get("confirmed")
    return command_result(
        request_id=request_id,
        action=action,
        confirmed=confirmed if isinstance(confirmed, bool) else None,
        spoken=spoken,
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
