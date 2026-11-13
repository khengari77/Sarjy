"""Deterministic context compilation.

``compile_turn_context`` is the authoritative compiler. It turns
permission-filtered history, memories and tool results into one revision's
canonical pre-provider message list plus a manifest.

It is pure: no I/O, no provider code and no mutation of its inputs. The
manifest, the canonical messages and the snapshot record all carry the same
``revision``; ``validate_assembly`` rejects any mixture.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from app.domain.contracts import ContextEntry, ContextSelection
from app.domain.provider_bounds import DEEPSEEK_MAX_REQUEST_BYTES, admission_reserve_bytes, provider_request_bytes

PROVIDER_CAPTURE = "canonical_pre_adapter"
DEFAULT_MAX_PROVIDER_BYTES = DEEPSEEK_MAX_REQUEST_BYTES
DEFAULT_RESERVE_BYTES = admission_reserve_bytes(1024)
DEFAULT_ALLOWED_ORIGINS: frozenset[str] = frozenset({"system", "history", "memory", "tool"})
DEFAULT_PERMITTED_TOOLS: frozenset[str] = frozenset({"get_current_weather"})
PROVIDER_ROLES = frozenset({"system", "user", "assistant"})
LOWER_TRUST_ROLES = frozenset({"memory", "tool"})

Origin = Literal["system", "history", "memory", "tool"]
CandidateRole = Literal["system", "user", "assistant", "memory", "tool"]
ProviderRole = Literal["system", "user", "assistant"]


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


def provider_role(role: CandidateRole) -> ProviderRole:
    """Map a selection role onto the only provider roles the boundary permits.

    Stored memory and external tool output are data, never instructions. They
    keep their provenance in ``ContextEntry.role`` but reach the provider as
    ordinary user-role content.
    """
    return cast(ProviderRole, role) if role in PROVIDER_ROLES else "user"


def dialogue_role(role: CandidateRole) -> Literal["user", "assistant"]:
    """Only the synthetic instructions may supply a privileged role.

    Callers supply history and current-turn candidates. Any role other than
    user or assistant is deterministically demoted to user, so a stray system
    (or memory/tool) role can never become provider instructions.
    """
    if role in ("user", "assistant"):
        return role
    return "user"


def untrusted_data_message(
    record_id: str, origin: Literal["memory", "tool"], text: str, source: str | None = None
) -> str:
    """Render one lower-trust record as an inert JSON envelope.

    JSON encoding the original text means nested quotes, delimiters and
    newlines cannot escape the data boundary. The short prefix keeps the
    message readable while the envelope carries provenance, the source id and,
    for tool output, the tool name.
    """
    label = "Memory" if origin == "memory" else "Tool result"
    payload: dict[str, Any] = {"id": record_id, "origin": origin, "text": text, "trust": "untrusted"}
    if source:
        payload["source"] = source
    envelope = canonical_json(payload)
    return f"{label} (untrusted {origin} data, id={record_id}): {envelope}"


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
    max_provider_bytes: int = DEFAULT_MAX_PROVIDER_BYTES,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
) -> CompiledContext:
    """Compile one revision's provider-bound messages.

    Selection rules, all enforced here rather than by callers:

    * an excluded ID never enters the canonical messages or the included set;
    * only allowlisted origins and active memories are eligible;
    * tool results are admitted only for allowlisted tool names;
    * memories and tool results are sorted by ID because their input order is
      not semantic, so a shuffled input yields byte-identical output;
    * history and the current turn keep caller order because turn order is
      semantic;
    * only the synthetic instructions entry may be a provider system message:
      history/current-turn roles other than user or assistant are demoted to
      user.
    """
    if revision < 0:
        raise ValueError("REVISION_INVALID")
    if not instructions:
        raise ValueError("INSTRUCTIONS_REQUIRED")
    candidates = (*history, *memories, *tool_results)
    excluded = _expand_exclusions(candidates, excluded_ids)
    config = freeze_json(pipeline or {})
    selected = _select_messages(
        instructions=instructions,
        history=history,
        memories=memories,
        tool_results=tool_results,
        current_turn=current_turn,
        excluded=excluded,
        allowed_origins=allowed_origins,
        permitted_tools=permitted_tools,
    )
    kept = _truncate(
        _dedupe(selected),
        max_provider_bytes,
        reserve_bytes,
        current_turn_id=current_turn.id if current_turn is not None and current_turn.id not in excluded else None,
    )
    messages, included_ids, selection_entries = _render(kept)
    if provider_request_bytes(messages) + reserve_bytes > max_provider_bytes:
        # Defensive: _truncate is authoritative, so this should be unreachable.
        raise ValueError("CONTEXT_BUDGET_EXCEEDED")

    prompt = canonical_messages(messages)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    manifest = _manifest(
        revision=revision,
        current_turn=current_turn,
        config=config,
        included_ids=included_ids,
        excluded=excluded,
        prompt=prompt,
        digest=digest,
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


def _expand_exclusions(candidates: Sequence[ContextCandidate], excluded_ids: Iterable[str]) -> frozenset[str]:
    blocked = set(excluded_ids)
    while True:
        derived = {item.id for item in candidates if item.dependencies & blocked}
        if derived <= blocked:
            return frozenset(blocked)
        blocked.update(derived)


def _eligible(
    candidate: ContextCandidate,
    *,
    excluded: frozenset[str],
    allowed_origins: frozenset[str],
    permitted_tools: frozenset[str],
) -> bool:
    if candidate.id in excluded or not candidate.text:
        return False
    if candidate.origin not in allowed_origins:
        return False
    if candidate.origin == "memory" and candidate.status != "active":
        return False
    return candidate.origin != "tool" or candidate.source in permitted_tools


def _select_messages(
    *,
    instructions: str,
    history: Sequence[ContextCandidate],
    memories: Sequence[ContextCandidate],
    tool_results: Sequence[ContextCandidate],
    current_turn: ContextCandidate | None,
    excluded: frozenset[str],
    allowed_origins: frozenset[str],
    permitted_tools: frozenset[str],
) -> list[tuple[str, CandidateRole, str]]:
    def eligible(candidate: ContextCandidate) -> bool:
        return _eligible(
            candidate,
            excluded=excluded,
            allowed_origins=allowed_origins,
            permitted_tools=permitted_tools,
        )

    selected: list[tuple[str, CandidateRole, str]] = [("system", "system", instructions)]
    selected.extend(
        (
            candidate.id,
            "memory",
            untrusted_data_message(candidate.id, "memory", candidate.text, candidate.source),
        )
        for candidate in sorted(memories, key=lambda item: item.id)
        if eligible(candidate)
    )
    selected.extend(
        (candidate.id, dialogue_role(candidate.role), candidate.text) for candidate in history if eligible(candidate)
    )
    selected.extend(
        (candidate.id, "tool", untrusted_data_message(candidate.id, "tool", candidate.text, candidate.source))
        for candidate in sorted(tool_results, key=lambda item: item.id)
        if eligible(candidate)
    )
    if current_turn is not None and current_turn.id not in excluded:
        selected.append((current_turn.id, dialogue_role(current_turn.role), current_turn.text))
    return selected


def _dedupe(selected: Sequence[tuple[str, CandidateRole, str]]) -> list[tuple[str, CandidateRole, str]]:
    deduped: list[tuple[str, CandidateRole, str]] = []
    seen: set[str] = set()
    for item in selected:
        if item[0] in seen:
            continue
        seen.add(item[0])
        deduped.append(item)
    return deduped


def _provider_messages(
    items: Sequence[tuple[str, CandidateRole, str]],
) -> list[dict[str, str]]:
    return [{"role": provider_role(role), "content": text} for _, role, text in items]


def _truncate(
    selected: Sequence[tuple[str, CandidateRole, str]],
    max_provider_bytes: int,
    reserve_bytes: int,
    *,
    current_turn_id: str | None = None,
) -> list[tuple[str, CandidateRole, str]]:
    def fits(items: Sequence[tuple[str, CandidateRole, str]]) -> bool:
        return provider_request_bytes(_provider_messages(items)) + reserve_bytes <= max_provider_bytes

    system = [item for item in selected if item[1] == "system"]
    if any(len(item[2]) > 16000 for item in system):
        raise ValueError("CONTEXT_ENTRY_TOO_LONG")
    if not fits(system):
        raise ValueError("CONTEXT_BUDGET_EXCEEDED")
    retained = list(system)
    if current_turn_id is not None:
        current_item = next((item for item in selected if item[0] == current_turn_id), None)
        if current_item is not None:
            if len(current_item[2]) > 16000:
                raise ValueError("CONTEXT_ENTRY_TOO_LONG")
            # The latest user turn is never silently replaced by older context.
            if not fits([*retained, current_item]):
                raise ValueError("CONTEXT_BUDGET_EXCEEDED")
            retained.append(current_item)
    for item in reversed(selected):
        if item[0] == current_turn_id or item[1] == "system":
            continue
        if fits([*retained, item]):
            retained.append(item)
    retained_ids = {item[0] for item in retained}
    return [item for item in selected if item[0] in retained_ids]


def _render(
    kept: Sequence[tuple[str, CandidateRole, str]],
) -> tuple[list[dict[str, str]], list[str], list[ContextEntry]]:
    messages: list[dict[str, str]] = []
    included_ids: list[str] = []
    selection_entries: list[ContextEntry] = []
    for record_id, role, text in kept:
        if len(text) > 16000:
            # ContextEntry and ModelRequest share this maximum. Failing here is
            # preferable to persisting a selection that cannot be replayed.
            raise ValueError("CONTEXT_ENTRY_TOO_LONG")
        messages.append({"role": provider_role(role), "content": text})
        included_ids.append(record_id)
        selection_entries.append(ContextEntry(id=record_id, role=role, text=text))
    return messages, included_ids, selection_entries


def _manifest(
    *,
    revision: int,
    current_turn: ContextCandidate | None,
    config: Mapping[str, Any],
    included_ids: list[str],
    excluded: frozenset[str],
    prompt: str,
    digest: str,
) -> Mapping[str, Any]:
    return MappingProxyType(
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


__all__ = [
    "CompiledContext",
    "ContextCandidate",
    "DEFAULT_ALLOWED_ORIGINS",
    "DEFAULT_MAX_PROVIDER_BYTES",
    "DEFAULT_PERMITTED_TOOLS",
    "DEFAULT_RESERVE_BYTES",
    "LOWER_TRUST_ROLES",
    "PROVIDER_CAPTURE",
    "canonical_json",
    "canonical_messages",
    "compile_turn_context",
    "dialogue_role",
    "prompt_sha256",
    "provider_role",
    "untrusted_data_message",
    "validate_assembly",
]
