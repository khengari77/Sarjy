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
    memory_undo_requested,
    parse_exact_command,
    parse_exclusion_confirmation,
    parse_memory_exclusion_target,
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
    SetMemoryMode,
    SetSpeakingSpeed,
    SetTheme,
    UserSettings,
)
from app.domain.ui_routing import (
    UiActionDecision,
    UiClarificationDecision,
    UiVoiceGenderDecision,
    evaluate_ui_classification,
    ui_text_is_eligible,
)
from app.services.actions import dispatch_action, dispatch_setting
from app.services.context_operations import ContextOperationError, MemorySearcher, has_content_words, keyword_search
from app.services.jev_ui import UiClassifier
from app.services.voice_exclusions import (
    discard_pending,
    global_retraction_refusal,
    hide_target,
    propose_restore,
    resolve_confirmation,
    undo_last_hide,
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
    ui_classifier: UiClassifier | None = None,
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
    # Consent is one-shot: a pending proposal answers only the very next turn, so a
    # later "yes" to some other question can never apply it.
    await discard_pending(db, owner_id=owner_id, conversation_id=conversation_id)

    resolved = await _resolve_action(text, request_id, catalog, approved_voice_ids, ui_classifier)
    if isinstance(resolved, CommandResult):
        return resolved
    action, voice_name = resolved
    if action is None:
        return await _unsupported_result(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            text=text,
        )

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
    if isinstance(action, SetMemoryMode):
        return await _run_setting(
            db,
            owner_id=owner_id,
            request_id=f"memory_mode:{settings_revision}:{action.mode}",
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


async def _resolve_action(
    text: str, request_id: str, catalog, approved: frozenset[str] | None, ui_classifier: UiClassifier | None
) -> tuple[Action | None, str | None] | CommandResult:
    """Exact grammar first, then voice names, then Jev (only on a full miss)."""
    action = parse_exact_command(text)
    if action is not None:
        return action, None
    voice = await _voice_selection(text, request_id, catalog, approved)
    if voice is None and ui_classifier is not None:
        voice = await _jev_ui_action(ui_classifier, text, request_id, catalog, approved)
    if voice is None or isinstance(voice, CommandResult):
        return voice if voice is not None else (None, None)
    return voice


async def _jev_ui_action(
    classifier: UiClassifier, text: str, request_id: str, catalog, approved: frozenset[str] | None
) -> tuple[Action, str | None] | CommandResult | None:
    """Ask Jev only after every deterministic path missed; any failure is a miss."""
    if not ui_text_is_eligible(text) or parse_exclusion_confirmation(text) is not None:
        return None
    try:
        classification = await classifier.classify(request_id=request_id, instruction=text)
    except Exception:
        return None
    if classification is None:
        return None
    decision = evaluate_ui_classification(classification)
    if isinstance(decision, UiClarificationDecision):
        return unsupported_result(request_id=request_id, detail="UI_MULTIPLE_COMMANDS", spoken=decision.spoken)
    if isinstance(decision, UiActionDecision):
        return decision.action, None
    if isinstance(decision, UiVoiceGenderDecision):
        return await _gendered_voice(decision.gender, request_id, catalog, approved)
    return None


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
    if memory_undo_requested(text):
        return await _run_memory_command(
            undo_last_hide(db, owner_id=owner_id, conversation_id=conversation_id, request_id=request_id),
            request_id=request_id,
        )
    target = parse_memory_exclusion_target(text)
    # A target made only of stopwords ("all of it", "this chat") names nothing, and an empty
    # keyword query matches every memory; never let it hide one.
    if target is None or not has_content_words(target):
        return None
    return await _run_memory_command(
        hide_target(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            target=target,
            request_id=request_id,
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
        return await _gendered_voice(gender, request_id, catalog, approved)
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


async def _gendered_voice(
    gender: str, request_id: str, catalog, approved: frozenset[str] | None
) -> tuple[SelectApprovedVoice, str] | CommandResult:
    found = await _resolve_gendered_voice(catalog, gender, approved)
    if found is None:
        return unsupported_result(
            request_id=request_id,
            detail="VOICE_GENDER_NOT_FOUND",
            spoken=f"I don't have an approved {gender} voice, so nothing was changed.",
        )
    return SelectApprovedVoice(voice_id=found.id), found.name


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
