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

from app.domain.secrets import secret_matches

# Budget-like records are never removed by a bulk "forget" proposal.  The
# architecture's signature interaction is "forget the trip but keep the
# budget"; treating budget as a default keep-exception makes that safe even if
# the parser only forwards an explicit keep token for the wording it understood.
BUDGET_PATTERN = re.compile(
    r"\b(budget|budgets|cost|costs|price|prices|spend|spending|sar|usd|dollars?)\b"
    r"|[$€£]"
    r"|ميزانية|ميزانيه|تكلفة|تكلفه|ريال|دولار",
    re.IGNORECASE,
)

KEEP_REASON_EXPLICIT_ID = "explicit keep id"
KEEP_REASON_EXPLICIT_TERM = "explicit keep exception"
KEEP_REASON_BUDGET = "protected budget"


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
    if candidate.is_budget:
        return KEEP_REASON_BUDGET
    return None


def select_candidates(
    candidates: tuple[MemoryCandidate, ...],
    keep: tuple[str, ...] = (),
) -> Selection:
    """Apply keep-exceptions exactly, without letting a keep id be dropped.

    ``keep`` entries are record ids first (exact match) and otherwise literal
    case-insensitive content filters.  Budget-classified records are preserved
    by policy.  Duplicate ids are collapsed so diffs stay canonical.
    """
    keep_terms = tuple(term.strip() for term in keep if term and term.strip())
    preserved: dict[str, PreservedRecord] = {}
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
        else:
            excluded.add(candidate.id)
    kept_ids = frozenset(preserved)
    return Selection(
        matches=tuple(ordered),
        preserved=tuple(preserved[key] for key in sorted(preserved)),
        excluded_ids=frozenset(excluded - kept_ids),
        kept_ids=kept_ids,
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
    if not secret_matches(signature, expected):
        raise ValueError("PROPOSAL_INVALID")
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("PROPOSAL_INVALID") from exc
    if not isinstance(payload, dict):
        raise ValueError("PROPOSAL_INVALID")
    return payload
