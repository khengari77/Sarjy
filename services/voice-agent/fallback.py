"""Pure fallback eligibility for one pre-output provider failure.

The worker may make one primary attempt and at most one fallback attempt.  This
module owns only the policy decision; provider construction, budget reservation
and streaming remain in the worker shell.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.domain.contracts import ModelRequest
from generation import GenerationEvent

TRANSIENT_ERROR_CODES: Final = frozenset({
    'connection_error',
    'incomplete_response',
    'timeout',
    'rate_limit',
    'provider_error',
})
TRANSIENT_STATUS_CODES: Final = frozenset({408, 429, 500, 502, 503, 504})
BLOCKED_ERROR_CODES: Final = frozenset({
    'authentication',
    'invalid_request',
    'insufficient_balance',
    'cancelled',
})


@dataclass(frozen=True)
class FallbackDecision:
    allowed: bool
    reason: str


def fallback_decision(
    failure: GenerationEvent,
    *,
    emitted_output: bool,
    fallback_configured: bool,
) -> FallbackDecision:
    """Decide whether a failed attempt may be followed by one fallback.

    Output includes any text or tool-call chunk already yielded to LiveKit.
    Usage-only progress is not output and may be discarded before fallback.
    """
    if not fallback_configured:
        return FallbackDecision(False, 'FALLBACK_NOT_CONFIGURED')
    if failure.kind != 'error':
        return FallbackDecision(False, 'NOT_A_GENERATION_ERROR')
    if emitted_output or failure.partial:
        return FallbackDecision(False, 'OUTPUT_ALREADY_EMITTED')
    if failure.error_code in BLOCKED_ERROR_CODES:
        return FallbackDecision(False, 'INELIGIBLE_FAILURE')
    if failure.status_code is not None:
        if failure.status_code in TRANSIENT_STATUS_CODES:
            return FallbackDecision(True, 'TRANSIENT_PROVIDER_FAILURE')
        # A permanent HTTP status always wins over a generic provider_error code.
        return FallbackDecision(False, 'INELIGIBLE_FAILURE')
    if failure.error_code in TRANSIENT_ERROR_CODES:
        return FallbackDecision(True, 'TRANSIENT_PROVIDER_FAILURE')
    return FallbackDecision(False, 'INELIGIBLE_FAILURE')


def fallback_eligible(
    failure: GenerationEvent,
    *,
    emitted_output: bool,
    fallback_configured: bool,
) -> bool:
    return fallback_decision(
        failure,
        emitted_output=emitted_output,
        fallback_configured=fallback_configured,
    ).allowed


def next_attempt(request: ModelRequest, attempt_id: str) -> ModelRequest:
    """Return a copied request with a fresh attempt identity, unchanged otherwise."""
    return request.model_copy(update={'attempt_id': attempt_id})


__all__ = [
    'BLOCKED_ERROR_CODES',
    'FallbackDecision',
    'TRANSIENT_ERROR_CODES',
    'TRANSIENT_STATUS_CODES',
    'fallback_decision',
    'fallback_eligible',
    'next_attempt',
]
