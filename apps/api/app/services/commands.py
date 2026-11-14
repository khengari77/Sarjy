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
    parse_exact_command,
    parse_voice_gender,
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
    SetTheme,
    UserSettings,
)
from app.services.actions import dispatch_action, dispatch_setting
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
    approved_voice_ids: frozenset[str] | None = None,
    handlers,
) -> CommandResult | None:
    """Interpret and, when deterministic, execute exactly one action.

    ``None`` means "not a command": the caller keeps the guarded
    conversational path.  A returned result describes a committed effect or an
    explicit unsupported/failed/unknown outcome.  One call never applies more
    than one effect.
    """
    action = parse_exact_command(text)
    voice_name: str | None = None
    if action is None:
        voice = await _voice_selection(text, request_id, catalog, approved_voice_ids)
        if isinstance(voice, CommandResult):
            return voice
        if voice is not None:
            action, voice_name = voice
    if action is None:
        unsupported = unsupported_command(text)
        if unsupported is not None:
            detail, spoken = unsupported
            return unsupported_result(request_id=request_id, detail=detail, spoken=spoken)
        return None

    # Theme is presentation-only and belongs to one browser, so there is nothing
    # to persist server-side. The result carries the hint; the client applies it
    # once through the shell's theme session, which owns storage and resolution.
    if isinstance(action, SetTheme):
        return command_result(request_id=request_id, action=action, theme=action.theme)

    # Speed, captions and voice are owner-scoped preferences: exactly one writer,
    # POST /v1/settings/actions, never the conversation-scoped dispatcher. The
    # worker reads the frozen snapshot at the next turn, so a committed speed is
    # heard from the next reply, not mid-sentence.
    if isinstance(action, (AdjustSpeakingSpeed, SetSpeakingSpeed)):
        amount = f"{action.delta:+.1f}" if isinstance(action, AdjustSpeakingSpeed) else f"{action.speed:.1f}"
        return await _run_setting(
            db,
            owner_id=owner_id,
            request_id=f"speed:{settings_revision}:{amount}",
            settings_revision=settings_revision,
            action=action,
            catalog=catalog,
            approved_voice_ids=approved_voice_ids,
            spoken=None,
        )
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


async def _voice_selection(
    text: str, request_id: str, catalog, approved: frozenset[str] | None
) -> tuple[SelectApprovedVoice, str] | CommandResult | None:
    """Resolve a spoken voice request to an approved id, or an honest refusal.

    ``None`` means the text names no voice. A gender request is checked before a
    name so "a male" is never searched as a voice called "a male".
    """
    gender = parse_voice_gender(text)
    if gender is not None:
        found = await _resolve_gendered_voice(catalog, gender, approved)
        if found is None:
            return unsupported_result(
                request_id=request_id,
                detail="VOICE_GENDER_NOT_FOUND",
                spoken=f"I don't have an approved {gender} voice, so nothing was changed.",
            )
        return SelectApprovedVoice(voice_id=found.id), found.name
    name = parse_voice_selection(text)
    if name is None:
        return None
    resolved = await _resolve_voice(catalog, name)
    if resolved is None:
        return unsupported_result(
            request_id=request_id,
            detail="VOICE_NOT_FOUND",
            spoken=f"I couldn't find an approved voice called {name}.",
        )
    if approved is not None and resolved.id not in approved:
        return unsupported_result(
            request_id=request_id,
            detail="VOICE_NOT_APPROVED",
            spoken=f"The voice called {resolved.name} isn't approved.",
        )
    return SelectApprovedVoice(voice_id=resolved.id), resolved.name


async def _resolve_gendered_voice(catalog, gender: str, approved: frozenset[str] | None):
    find = getattr(catalog, "find_by_trait", None)
    if find is None or not approved:
        return None
    try:
        voice = await find("gender", gender, approved)
    except VoiceCatalogError:
        return None
    # Defence in depth: the id must be approved and carry the requested label.
    if voice is None or voice.id not in approved or voice.gender != gender:
        return None
    return voice


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
