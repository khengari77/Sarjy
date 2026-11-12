"""Read-only compatibility for frozen pre-ModelRequest turn snapshots."""

from collections.abc import Mapping
from uuid import uuid4

from app.domain.context import validate_assembly
from app.domain.contracts import ContextEntry, ContextSelection, ModelRequest
from app.domain.policy import pipeline_policy


class SnapshotCompatibilityError(ValueError):
    code = "SNAPSHOT_INCOMPATIBLE"


def snapshot_request(snapshot, turn_id):
    try:
        if not isinstance(snapshot, Mapping):
            raise ValueError("INVALID_SNAPSHOT")
        if "model_request" in snapshot:
            request = ModelRequest.model_validate(snapshot["model_request"])
            if (
                request.turn_id != turn_id
                or request.selection.revision != snapshot["revision"]
                or [message.model_dump() for message in request.messages] != snapshot["messages"]
            ):
                raise ValueError("MODEL_SNAPSHOT_MISMATCH")
            return request
        if not isinstance(snapshot.get("manifest"), Mapping) or not isinstance(
            snapshot.get("pipeline_config"), Mapping
        ):
            raise ValueError("INVALID_LEGACY_METADATA")
        validate_assembly(snapshot["revision"], snapshot["manifest"], snapshot)
        for key in ("included_record_ids", "excluded_record_ids"):
            if list(snapshot[key]) != list(snapshot["manifest"][key]):
                raise ValueError("INVALID_LEGACY_SELECTION")
        messages = snapshot["messages"]
        identifiers = snapshot["included_record_ids"]
        if len(messages) != len(identifiers) or not messages or messages[0]["role"] != "system":
            raise ValueError("INVALID_LEGACY_MESSAGES")
        if snapshot["manifest"].get("turn_id") != turn_id:
            raise ValueError("INVALID_LEGACY_TURN")
        pipeline = snapshot["pipeline_config"]
        return ModelRequest(
            turn_id=turn_id,
            attempt_id=str(uuid4()),
            instructions=messages[0]["content"],
            messages=messages,
            selection=ContextSelection(
                revision=snapshot["revision"],
                included=tuple(
                    ContextEntry(id=identifier, role=message["role"], text=message["content"])
                    for identifier, message in zip(identifiers, messages, strict=False)
                ),
                excluded_ids=frozenset(snapshot["excluded_record_ids"]),
            ),
            response_policy=pipeline_policy(pipeline),
            settings=None,
            settings_revision=None,
            language=pipeline.get("llm", {}).get("language", "en"),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise SnapshotCompatibilityError("SNAPSHOT_INCOMPATIBLE") from exc
