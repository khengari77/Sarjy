"""Read-only compatibility for frozen pre-ModelRequest turn snapshots.

Older snapshots promoted stored memory and tool output into provider ``system``
messages. Replay never restores that privileged projection: a compatible
legacy record is validated against its stored identity, order, text, revision
and canonical hash, then re-rendered through the same lower-trust envelope the
current compiler uses. The stored snapshot is never mutated.

The top-level ``canonical_prompt_sha256`` is mandatory for ``model_request``
replay. When ``manifest``/``pipeline_config`` are present the full assembly is
validated too; a minimal snapshot without them is rejected only for missing
identity/hash, not silently trusted beyond the top-level fields.
"""

from collections.abc import Mapping, Sequence
from uuid import uuid4

from app.domain.context import (
    LOWER_TRUST_ROLES,
    prompt_sha256,
    provider_role,
    untrusted_data_message,
    validate_assembly,
)
from app.domain.contracts import ContextEntry, ContextSelection, ModelRequest
from app.domain.policy import pipeline_policy

INSTRUCTION_ROLES = frozenset({"system", "user", "assistant"})
MEMORY_PREFIX = "Memory: "
TOOL_PREFIX = "Tool result ("
TOOL_SEPARATOR = "): "


class SnapshotCompatibilityError(ValueError):
    code = "SNAPSHOT_INCOMPATIBLE"


def snapshot_request(snapshot, turn_id):
    try:
        if not isinstance(snapshot, Mapping):
            raise ValueError("INVALID_SNAPSHOT")
        if "model_request" in snapshot:
            return _model_request_replay(snapshot, turn_id)
        return _metadata_replay(snapshot, turn_id)
    except (KeyError, ValueError, TypeError) as exc:
        raise SnapshotCompatibilityError("SNAPSHOT_INCOMPATIBLE") from exc


def _model_request_replay(snapshot: Mapping, turn_id: str) -> ModelRequest:
    request = ModelRequest.model_validate(snapshot["model_request"])
    if request.turn_id != turn_id or request.selection.revision != snapshot["revision"]:
        raise ValueError("MODEL_SNAPSHOT_MISMATCH")
    messages = _message_list(snapshot["messages"])
    if [message.model_dump() for message in request.messages] != messages:
        raise ValueError("MODEL_SNAPSHOT_MISMATCH")
    _validate_hash(snapshot, messages)
    _validate_manifest(snapshot)
    _validate_selection_identity(snapshot, request.selection)
    safe, converted = _safe_projection(request.selection.included, messages)
    if not converted:
        return request
    selection = _rebuilt_selection(request.selection, safe)
    return ModelRequest.model_validate(
        {
            **request.model_dump(mode="json"),
            "messages": safe,
            "selection": selection.model_dump(mode="json"),
        }
    )


def _metadata_replay(snapshot: Mapping, turn_id: str) -> ModelRequest:
    if not isinstance(snapshot.get("manifest"), Mapping) or not isinstance(snapshot.get("pipeline_config"), Mapping):
        raise ValueError("INVALID_LEGACY_METADATA")
    validate_assembly(snapshot["revision"], snapshot["manifest"], snapshot)
    for key in ("included_record_ids", "excluded_record_ids"):
        if list(snapshot[key]) != list(snapshot["manifest"][key]):
            raise ValueError("INVALID_LEGACY_SELECTION")
    messages = _message_list(snapshot["messages"])
    identifiers = list(snapshot["included_record_ids"])
    if len(messages) != len(identifiers) or not messages or messages[0]["role"] != "system":
        raise ValueError("INVALID_LEGACY_MESSAGES")
    if snapshot["manifest"].get("turn_id") != turn_id:
        raise ValueError("INVALID_LEGACY_TURN")

    entries = tuple(
        ContextEntry(
            id=identifier,
            role=_legacy_role(index, message),
            text=message["content"],
        )
        for index, (identifier, message) in enumerate(zip(identifiers, messages, strict=True))
    )
    safe, _ = _safe_projection(entries, messages)
    pipeline = snapshot["pipeline_config"]
    return ModelRequest(
        turn_id=turn_id,
        attempt_id=str(uuid4()),
        instructions=messages[0]["content"],
        messages=safe,
        selection=ContextSelection(
            revision=snapshot["revision"],
            included=entries,
            excluded_ids=frozenset(snapshot["excluded_record_ids"]),
        ),
        response_policy=pipeline_policy(pipeline),
        settings=None,
        settings_revision=None,
        language=pipeline.get("llm", {}).get("language", "en"),
    )


def _message_list(messages) -> list[dict[str, str]]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise ValueError("INVALID_MESSAGES")
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("INVALID_MESSAGES")
        role, content = message.get("role"), message.get("content")
        if role not in INSTRUCTION_ROLES or not isinstance(content, str):
            raise ValueError("INVALID_MESSAGES")
        normalized.append({"role": role, "content": content})
    return normalized


def _validate_hash(snapshot: Mapping, messages: Sequence[Mapping[str, str]]) -> None:
    if "canonical_prompt_sha256" not in snapshot:
        raise ValueError("MISSING_CANONICAL_HASH")
    if prompt_sha256(messages) != snapshot["canonical_prompt_sha256"]:
        raise ValueError("HASH_MISMATCH_SNAPSHOT")


def _validate_manifest(snapshot: Mapping) -> None:
    manifest, config = snapshot.get("manifest"), snapshot.get("pipeline_config")
    if manifest is None and config is None:
        return
    if not isinstance(manifest, Mapping) or not isinstance(config, Mapping):
        raise ValueError("INVALID_SNAPSHOT_METADATA")
    validate_assembly(snapshot["revision"], manifest, snapshot)


def _validate_selection_identity(snapshot: Mapping, selection: ContextSelection) -> None:
    if "included_record_ids" in snapshot and [entry.id for entry in selection.included] != list(
        snapshot["included_record_ids"]
    ):
        raise ValueError("SELECTION_ID_MISMATCH")
    if "excluded_record_ids" in snapshot and set(selection.excluded_ids) != set(snapshot["excluded_record_ids"]):
        raise ValueError("SELECTION_EXCLUSION_MISMATCH")


def _legacy_role(index: int, message: Mapping[str, str]) -> str:
    role = message["role"]
    if index == 0:
        if role != "system":
            raise ValueError("INVALID_LEGACY_MESSAGES")
        return "system"
    if role in ("user", "assistant"):
        return role
    if role == "system":
        content = message["content"]
        if content.startswith(MEMORY_PREFIX):
            return "memory"
        if content.startswith(TOOL_PREFIX) and TOOL_SEPARATOR in content[len(TOOL_PREFIX) :]:
            return "tool"
        raise ValueError("UNATTRIBUTED_SYSTEM_MESSAGE")
    raise ValueError("INVALID_LEGACY_MESSAGES")


def _safe_projection(
    entries: Sequence[ContextEntry], messages: Sequence[Mapping[str, str]]
) -> tuple[list[dict[str, str]], bool]:
    """Validate stored identity/order/text and re-render lower-trust entries."""
    if len(entries) != len(messages) or not entries:
        raise ValueError("SELECTION_MESSAGE_MISMATCH")
    if entries[0].role != "system" or messages[0]["role"] != "system":
        raise ValueError("INSTRUCTIONS_NOT_SYSTEM")
    safe: list[dict[str, str]] = []
    converted = False
    for entry, message in zip(entries, messages, strict=True):
        if entry.text != message["content"]:
            raise ValueError("SELECTION_TEXT_MISMATCH")
        expected = provider_role(entry.role)
        role = message["role"]
        if role == expected:
            safe.append({"role": role, "content": message["content"]})
            continue
        if entry.role not in LOWER_TRUST_ROLES or role != "system":
            raise ValueError("SELECTION_ROLE_MISMATCH")
        text, source = _legacy_record(entry)
        safe.append(
            {
                "role": "user",
                "content": untrusted_data_message(entry.id, entry.role, text, source),
            }
        )
        converted = True
    return safe, converted


def _rebuilt_selection(selection: ContextSelection, messages: Sequence[Mapping[str, str]]) -> ContextSelection:
    entries = tuple(
        ContextEntry(id=entry.id, role=message["role"], text=message["content"])
        for entry, message in zip(selection.included, messages, strict=True)
    )
    return ContextSelection(revision=selection.revision, included=entries, excluded_ids=selection.excluded_ids)


def _legacy_record(entry: ContextEntry) -> tuple[str, str | None]:
    if entry.role == "memory" and entry.text.startswith(MEMORY_PREFIX):
        return entry.text[len(MEMORY_PREFIX) :], None
    if entry.role == "tool" and entry.text.startswith(TOOL_PREFIX):
        end = entry.text.find(TOOL_SEPARATOR, len(TOOL_PREFIX))
        if end > len(TOOL_PREFIX):
            return entry.text[end + len(TOOL_SEPARATOR) :], entry.text[len(TOOL_PREFIX) : end]
    raise ValueError("UNATTRIBUTED_SYSTEM_MESSAGE")
