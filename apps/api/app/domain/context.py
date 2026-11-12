"""Deterministic context compilation.

Two entry points live here:

* ``compile_turn_context`` is the authoritative Day 2 compiler. It turns
  permission-filtered history, memories and tool results into one revision's
  canonical pre-provider message list plus a manifest.
* ``compile_context`` is the pre-Day-2 helper kept for the worker's existing
  call sites and regression tests. It is a thin, deterministic subset.

Both are pure: no I/O, no provider code and no mutation of their inputs. The
manifest, the canonical messages and the snapshot record all carry the same
``revision``; ``validate_assembly`` rejects any mixture.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from app.domain.contracts import ContextEntry, ContextSelection

PROVIDER_CAPTURE = "canonical_pre_adapter"
DEFAULT_MAX_CHARS = 16000
DEFAULT_ALLOWED_ORIGINS: frozenset[str] = frozenset({"system", "history", "memory", "tool"})
DEFAULT_PERMITTED_TOOLS: frozenset[str] = frozenset({"get_current_weather"})
PROVIDER_ROLES = frozenset({"system", "user", "assistant"})

Origin = Literal["system", "history", "memory", "tool"]
CandidateRole = Literal["system", "user", "assistant", "memory", "tool"]


@dataclass(frozen=True)
class ContextRecord:
    id: str
    role: str
    content: str


@dataclass(frozen=True)
class ContextCandidate:
    """One candidate record before permission filtering.

    ``origin`` is the access class (history, memory, tool, system) and
    ``source`` names a tool or provenance channel that the policy allowlists.
    """

    id: str
    role: CandidateRole
    text: str
    origin: Origin
    source: str | None = None
    status: Literal["active", "superseded", "inactive"] = "active"
    dependencies: frozenset[str] = frozenset()


def freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def json_containers(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: json_containers(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [json_containers(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Stable JSON bytes-as-text: sorted keys, no incidental whitespace."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_messages(messages: Sequence[Mapping[str, str]]) -> str:
    return canonical_json([{"role": message["role"], "content": message["content"]} for message in messages])


def prompt_sha256(messages: Sequence[Mapping[str, str]]) -> str:
    return hashlib.sha256(canonical_messages(messages).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CompiledContext:
    revision: int
    messages: tuple[Mapping[str, str], ...]
    included_ids: tuple[str, ...]
    excluded_ids: frozenset[str]
    pipeline_config: Mapping[str, Any]
    manifest: Mapping[str, Any]
    selection: ContextSelection
    canonical_prompt: str
    canonical_prompt_sha256: str

    def to_snapshot(self) -> dict[str, Any]:
        """JSON-ready record shared by the turn snapshot and context_snapshots."""
        messages = [dict(message) for message in self.messages]
        return {
            "revision": self.revision,
            "messages": messages,
            "included_record_ids": list(self.included_ids),
            "excluded_record_ids": sorted(self.excluded_ids),
            "canonical_prompt_sha256": self.canonical_prompt_sha256,
            "manifest": dict(self.manifest),
            "pipeline_config": json_containers(self.pipeline_config),
            "provider_request_capture": PROVIDER_CAPTURE,
        }


def compile_turn_context(
    *,
    revision: int,
    instructions: str,
    history: Sequence[ContextCandidate] = (),
    memories: Sequence[ContextCandidate] = (),
    tool_results: Sequence[ContextCandidate] = (),
    current_turn: ContextCandidate | None = None,
    excluded_ids: Iterable[str] = (),
    pipeline: Mapping[str, Any] | None = None,
    allowed_origins: frozenset[str] = DEFAULT_ALLOWED_ORIGINS,
    permitted_tools: frozenset[str] = DEFAULT_PERMITTED_TOOLS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> CompiledContext:
    """Compile one revision's provider-bound messages.

    Selection rules, all enforced here rather than by callers:

    * an excluded ID never enters the canonical messages or the included set;
    * only allowlisted origins and active memories are eligible;
    * tool results are admitted only for allowlisted tool names;
    * memories and tool results are sorted by ID because their input order is
      not semantic, so a shuffled input yields byte-identical output;
    * history and the current turn keep caller order because turn order is
      semantic.
    """
    if revision < 0:
        raise ValueError("REVISION_INVALID")
    if not instructions:
        raise ValueError("INSTRUCTIONS_REQUIRED")
    excluded = frozenset(excluded_ids)
    config = freeze_json(pipeline or {})
    blocked = set(excluded)
    candidates = (*history, *memories, *tool_results)
    while True:
        derived = {item.id for item in candidates if item.dependencies & blocked}
        if derived <= blocked:
            break
        blocked.update(derived)
    excluded = frozenset(blocked)

    def eligible(candidate: ContextCandidate) -> bool:
        if candidate.id in excluded or not candidate.text:
            return False
        if candidate.origin not in allowed_origins:
            return False
        if candidate.origin == "memory" and candidate.status != "active":
            return False
        return not (candidate.origin == "tool" and candidate.source not in permitted_tools)

    selected: list[tuple[str, CandidateRole, str]] = [("system", "system", instructions)]
    selected.extend(
        (candidate.id, "memory", f"Memory: {candidate.text}")
        for candidate in sorted(memories, key=lambda item: item.id)
        if eligible(candidate)
    )
    selected.extend((candidate.id, candidate.role, candidate.text) for candidate in history if eligible(candidate))
    selected.extend(
        (candidate.id, "tool", f"Tool result ({candidate.source}): {candidate.text}")
        for candidate in sorted(tool_results, key=lambda item: item.id)
        if eligible(candidate)
    )
    if current_turn is not None and current_turn.id not in excluded:
        selected.append((current_turn.id, current_turn.role, current_turn.text))

    deduped: list[tuple[str, CandidateRole, str]] = []
    seen: set[str] = set()
    for record_id, role, text in selected:
        if record_id in seen:
            continue
        seen.add(record_id)
        deduped.append((record_id, role, text))

    protected = [item for item in deduped if item[1] == "system"]
    remaining = max(0, max_chars - sum(len(item[2]) for item in protected))
    retained: list[tuple[str, CandidateRole, str]] = []
    current_id = current_turn.id if current_turn is not None else None
    if current_id is not None:
        current_item = next((item for item in deduped if item[0] == current_id), None)
        if current_item is not None:
            # The latest user turn is never silently replaced by older context.
            if len(current_item[2]) > remaining:
                raise ValueError("CONTEXT_BUDGET_EXCEEDED")
            retained.append(current_item)
            remaining -= len(current_item[2])
    for item in reversed(deduped):
        if item[0] == current_id:
            continue
        if item[1] == "system":
            retained.append(item)
        elif len(item[2]) <= remaining:
            retained.append(item)
            remaining -= len(item[2])
    kept = list(reversed(retained))

    messages: list[dict[str, str]] = []
    included_ids: list[str] = []
    selection_entries: list[ContextEntry] = []
    for record_id, role, text in kept:
        provider_role = role if role in PROVIDER_ROLES else "system"
        messages.append({"role": provider_role, "content": text})
        included_ids.append(record_id)
        selection_entries.append(ContextEntry(id=record_id, role=role, text=text[:16000]))

    prompt = canonical_messages(messages)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    manifest = MappingProxyType(
        {
            "turn_id": current_turn.id if current_turn is not None else "",
            "context_revision": revision,
            "pipeline_id": _pipeline_id(config),
            "pipeline_config_sha256": hashlib.sha256(canonical_json(json_containers(config)).encode()).hexdigest(),
            "included_record_ids": tuple(included_ids),
            "excluded_record_ids": tuple(sorted(excluded)),
            "canonical_prompt_sha256": digest,
            "token_estimate": max(1, len(prompt) // 4),
            "provider_request_capture": PROVIDER_CAPTURE,
        }
    )
    selection = ContextSelection(
        revision=revision,
        included=tuple(selection_entries),
        excluded_ids=excluded,
    )
    return CompiledContext(
        revision=revision,
        messages=tuple(MappingProxyType(dict(message)) for message in messages),
        included_ids=tuple(included_ids),
        excluded_ids=excluded,
        pipeline_config=config,
        manifest=manifest,
        selection=selection,
        canonical_prompt=prompt,
        canonical_prompt_sha256=digest,
    )


def _pipeline_id(pipeline: Mapping[str, Any]) -> str:
    llm = pipeline.get("llm", {}) if isinstance(pipeline, Mapping) else {}
    provider = llm.get("provider", "unknown") if isinstance(llm, Mapping) else "unknown"
    model = llm.get("model", "unknown") if isinstance(llm, Mapping) else "unknown"
    language = llm.get("language", "en") if isinstance(llm, Mapping) else "en"
    return f"{provider}/{model}/{language}"


def validate_assembly(revision: int, manifest: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    """One revision across manifest, snapshot record and canonical messages.

    Callers persist all three inside one transaction; this guard fails closed if
    a stale or partially assembled record is ever handed to the worker.
    """
    if manifest.get("context_revision") != revision:
        raise ValueError("REVISION_MISMATCH_MANIFEST")
    if snapshot.get("revision") != revision:
        raise ValueError("REVISION_MISMATCH_SNAPSHOT")
    try:
        actual = prompt_sha256(snapshot["messages"])
        config_hash = hashlib.sha256(canonical_json(snapshot["pipeline_config"]).encode()).hexdigest()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ASSEMBLY_INVALID") from exc
    if snapshot.get("canonical_prompt_sha256") != actual or manifest.get("canonical_prompt_sha256") != actual:
        raise ValueError("HASH_MISMATCH_SNAPSHOT")
    if manifest.get("pipeline_config_sha256") != config_hash:
        raise ValueError("CONFIG_MISMATCH_SNAPSHOT")


def compile_context(
    records: tuple[ContextRecord, ...],
    revision: int,
    excluded_ids: frozenset[str] = frozenset(),
    max_chars: int = 16000,
) -> dict[str, Any]:
    """Legacy deterministic compiler retained for existing worker paths."""
    selected = [r for r in records if r.id not in excluded_ids or r.role == "system"]
    protected = sum(len(r.content) for r in selected if r.role == "system")
    remaining = max(0, max_chars - protected)
    retained = []
    for record in reversed(selected):
        if record.role == "system":
            retained.append(record)
        elif len(record.content) <= remaining:
            retained.append(record)
            remaining -= len(record.content)
    selected = list(reversed(retained))
    messages = [{"role": r.role, "content": r.content} for r in selected]
    canonical = canonical_json(messages)
    return {
        "messages": messages,
        "revision": revision,
        "included_record_ids": [r.id for r in selected],
        "canonical_prompt_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "provider_request_capture": PROVIDER_CAPTURE,
    }


__all__ = [
    "CompiledContext",
    "ContextCandidate",
    "ContextRecord",
    "DEFAULT_ALLOWED_ORIGINS",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_PERMITTED_TOOLS",
    "PROVIDER_CAPTURE",
    "canonical_json",
    "canonical_messages",
    "compile_context",
    "compile_turn_context",
    "prompt_sha256",
    "validate_assembly",
]
