"""Pure context-command core: candidate selection, keep-exceptions and diffs.

Everything here is deterministic and side-effect free.  Authorization,
revision checks and persistence live in the service shell so they cannot be
bypassed by a caller of these helpers.
"""

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

# Budget-like content remains a display label and an ambiguity signal, never an
# unconditional keep authority.  Explicit per-operation keep exceptions (or a
# query that names budget/cost/price) decide whether a budget record survives.
BUDGET_PATTERN = re.compile(
    r"\b(budget|budgets|cost|costs|price|prices|spend|spending|sar|usd|dollars?)\b"
    r"|[$€£]"
    r"|ميزانية|ميزانيه|تكلفة|تكلفه|ريال|دولار",
    re.IGNORECASE,
)

KEEP_REASON_EXPLICIT_ID = "explicit keep id"
KEEP_REASON_EXPLICIT_TERM = "explicit keep exception"
KEEP_REASON_BUDGET_AMBIGUOUS = "budget requires clarification"


@dataclass(frozen=True)
class MemoryCandidate:
    """A search hit, before any exclusion decision is made."""

    id: str
    content: str
    source_turn_id: str | None = None
    score: float = 0.0
    kind: str = "memory"

    @property
    def is_budget(self) -> bool:
        return self.kind == "budget" or bool(BUDGET_PATTERN.search(self.content))


@dataclass(frozen=True)
class PreservedRecord:
    id: str
    content: str
    reason: str


@dataclass(frozen=True)
class Selection:
    """Immutable result of a preview: what would be excluded and what stays."""

    matches: tuple[MemoryCandidate, ...]
    preserved: tuple[PreservedRecord, ...]
    excluded_ids: frozenset[str]
    kept_ids: frozenset[str]
    ambiguous: tuple[PreservedRecord, ...] = ()

    @property
    def requires_clarification(self) -> bool:
        return bool(self.ambiguous)

    def diff(self, revision_before: int, revision_after: int | None = None) -> dict[str, Any]:
        return {
            "added": [],
            "removed": sorted(self.excluded_ids),
            "kept": sorted(self.kept_ids),
            "revision_before": revision_before,
            "revision_after": revision_before if revision_after is None else revision_after,
        }


def classify(content: str) -> str:
    return "budget" if BUDGET_PATTERN.search(content) else "memory"


def _keep_reason(candidate: MemoryCandidate, keep_terms: tuple[str, ...]) -> str | None:
    if candidate.id in keep_terms:
        return KEEP_REASON_EXPLICIT_ID
    lowered = candidate.content.casefold()
    if any(term.casefold() in lowered for term in keep_terms if term):
        return KEEP_REASON_EXPLICIT_TERM
    return None


def _explicitly_excluded(candidate: MemoryCandidate, exclude_terms: tuple[str, ...]) -> bool:
    if candidate.id in exclude_terms:
        return True
    lowered = candidate.content.casefold()
    return any(term.casefold() in lowered for term in exclude_terms if term)


def select_candidates(
    candidates: tuple[MemoryCandidate, ...],
    keep: tuple[str, ...] = (),
    query: str = "",
    exclude: tuple[str, ...] = (),
) -> Selection:
    """Apply keep-exceptions and explicit exclusions exactly.

    ``keep`` and ``exclude`` entries are record ids first (exact match) and
    otherwise literal case-insensitive content filters.  Keep authority wins
    over exclusion authority.  A budget-classified record is never removed
    merely because its content or the query mentions budget/cost/price: it is
    removed only when ``exclude`` explicitly authorises it, and otherwise it is
    preserved and surfaced as ambiguous so lost keep/parser state fails closed.
    ``query`` is retained for display and caller context; it grants no removal
    authority by itself.  Duplicate ids are collapsed so diffs stay canonical.
    """
    keep_terms = tuple(term.strip() for term in keep if term and term.strip())
    exclude_terms = tuple(term.strip() for term in exclude if term and term.strip())
    preserved: dict[str, PreservedRecord] = {}
    ambiguous: dict[str, PreservedRecord] = {}
    excluded: set[str] = set()
    seen: set[str] = set()
    ordered: list[MemoryCandidate] = []
    for candidate in candidates:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        ordered.append(candidate)
        reason = _keep_reason(candidate, keep_terms)
        if reason is not None:
            preserved[candidate.id] = PreservedRecord(candidate.id, candidate.content, reason)
        elif candidate.is_budget and not _explicitly_excluded(candidate, exclude_terms):
            ambiguous[candidate.id] = PreservedRecord(candidate.id, candidate.content, KEEP_REASON_BUDGET_AMBIGUOUS)
            preserved[candidate.id] = ambiguous[candidate.id]
        else:
            excluded.add(candidate.id)
    kept_ids = frozenset(preserved)
    return Selection(
        matches=tuple(ordered),
        preserved=tuple(preserved[key] for key in sorted(preserved)),
        excluded_ids=frozenset(excluded - kept_ids),
        kept_ids=kept_ids,
        ambiguous=tuple(ambiguous[key] for key in sorted(ambiguous)),
    )


def exclusion_diff(
    excluded_ids: frozenset[str], kept_ids: frozenset[str], revision_before: int, revision_after: int
) -> dict[str, Any]:
    return {
        "added": [],
        "removed": sorted(excluded_ids),
        "kept": sorted(kept_ids),
        "revision_before": revision_before,
        "revision_after": revision_after,
    }


def restore_diff(
    restored_ids: frozenset[str], still_excluded: frozenset[str], revision_before: int, revision_after: int
) -> dict[str, Any]:
    return {
        "added": sorted(restored_ids - still_excluded),
        "removed": [],
        "kept": sorted(still_excluded),
        "revision_before": revision_before,
        "revision_after": revision_after,
    }


def sign_proposal(payload: dict[str, Any], secret: str) -> str:
    """Encode the server-owned preview as an opaque HMAC-signed token.

    The database stays untouched during preview, so the exact id set lives in
    this token.  Confirmation only trusts the signed payload, never ids sent by
    the client.
    """
    if not secret:
        raise ValueError("CONTEXT_PROPOSALS_NOT_CONFIGURED")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_proposal(token: str, secret: str) -> dict[str, Any]:
    if not secret:
        raise ValueError("CONTEXT_PROPOSALS_NOT_CONFIGURED")
    try:
        body, signature = token.rsplit(".", 1)
    except (AttributeError, ValueError) as exc:
        raise ValueError("PROPOSAL_INVALID") from exc
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("PROPOSAL_INVALID")
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("PROPOSAL_INVALID") from exc
    if not isinstance(payload, dict):
        raise ValueError("PROPOSAL_INVALID")
    return payload
