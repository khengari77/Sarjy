"""Durable tool-operation claims and terminal outcomes.

The service never calls an external tool.  A caller claims an operation before
delivery, then records exactly one terminal state.  Replays return the stored
outcome; an in-progress claim blocks duplicates; unknown delivery is never
executed again.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.tool_operations import ToolReplayKind, arguments_fingerprint, replay_kind
from app.infrastructure.database import ToolCall


class ToolOperationError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ToolOperationClaim:
    kind: Literal['claimed', 'completed', 'pending', 'failed', 'unknown']
    result: dict | None = None
    error_code: str | None = None


async def claim_tool_operation(
    db: AsyncSession,
    *,
    turn_id: str,
    operation_key: str,
    name: str,
    arguments: Mapping[str, Any],
) -> ToolOperationClaim:
    """Atomically claim one operation; return an existing outcome when present.

    The key is bound to the tool name and normalized arguments. Reusing it for a
    different request is a conflict even when the turn matches.
    """
    fingerprint = arguments_fingerprint(arguments)
    dialect = db.bind.dialect.name if db.bind is not None else ''
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise ToolOperationError('TOOL_OPERATION_DATABASE_UNSUPPORTED', 503)

    result = await db.execute(insert(ToolCall).values(
        id=str(uuid4()),
        turn_id=turn_id,
        request_id=operation_key,
        name=name,
        state='pending',
        result=None,
        error_code=None,
        arguments_hash=fingerprint,
    ).on_conflict_do_nothing(index_elements=['request_id']))
    if result.rowcount == 1:
        return ToolOperationClaim('claimed')

    existing = await db.scalar(select(ToolCall).where(ToolCall.request_id == operation_key))
    if existing is None:
        raise ToolOperationError('TOOL_OPERATION_UNAVAILABLE', 503)
    if (
        existing.turn_id != turn_id
        or existing.name != name
        or existing.arguments_hash != fingerprint
    ):
        raise ToolOperationError('TOOL_OPERATION_CONFLICT', 409)

    kind = replay_kind(existing.state)
    if kind is ToolReplayKind.RESULT:
        return ToolOperationClaim('completed', result=existing.result)
    if kind is ToolReplayKind.IN_PROGRESS:
        return ToolOperationClaim('pending', error_code='TOOL_OPERATION_IN_PROGRESS')
    if kind is ToolReplayKind.FAILED:
        return ToolOperationClaim('failed', result=existing.result, error_code=existing.error_code)
    return ToolOperationClaim('unknown', error_code=existing.error_code or 'TOOL_OPERATION_UNKNOWN')


async def complete_tool_operation(
    db: AsyncSession,
    *,
    operation_key: str,
    result: dict,
) -> None:
    changed = await db.execute(update(ToolCall).where(
        ToolCall.request_id == operation_key,
        ToolCall.state == 'pending',
    ).values(state='completed', result=result, error_code=None))
    if changed.rowcount != 1:
        raise ToolOperationError('TOOL_OPERATION_NOT_PENDING', 409)


async def fail_tool_operation(
    db: AsyncSession,
    *,
    operation_key: str,
    error_code: str,
) -> None:
    changed = await db.execute(update(ToolCall).where(
        ToolCall.request_id == operation_key,
        ToolCall.state == 'pending',
    ).values(
        state='failed',
        result={'error': error_code},
        error_code=error_code,
    ))
    if changed.rowcount != 1:
        raise ToolOperationError('TOOL_OPERATION_NOT_PENDING', 409)


async def unknown_tool_operation(
    db: AsyncSession,
    *,
    operation_key: str,
    error_code: str,
) -> None:
    changed = await db.execute(update(ToolCall).where(
        ToolCall.request_id == operation_key,
        ToolCall.state == 'pending',
    ).values(state='unknown', result=None, error_code=error_code))
    if changed.rowcount != 1:
        raise ToolOperationError('TOOL_OPERATION_NOT_PENDING', 409)


__all__ = [
    'ToolOperationClaim',
    'ToolOperationError',
    'claim_tool_operation',
    'complete_tool_operation',
    'fail_tool_operation',
    'unknown_tool_operation',
]
