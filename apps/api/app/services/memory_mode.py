"""Effective memory mode for one conversation.

A conversation may override the account default.  NULL means "follow the
account default"; an unknown stored value fails closed to ``off``.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import MemoryMode
from app.infrastructure.database import Conversation, UserSetting

_VALID_MODES = frozenset({"off", "ask", "auto"})


def effective_memory_mode(conversation_mode: str | None, account_mode: str | None) -> MemoryMode:
    if conversation_mode is not None:
        if conversation_mode in _VALID_MODES:
            return cast(MemoryMode, conversation_mode)
        return "off"
    if account_mode is not None:
        if account_mode in _VALID_MODES:
            return cast(MemoryMode, account_mode)
        return "off"
    return "auto"


async def load_effective_memory_mode(db: AsyncSession, conversation_id: str) -> MemoryMode:
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        return "off"
    setting = await db.scalar(
        select(UserSetting)
        .where(UserSetting.owner_id == conversation.owner_id)
        .execution_options(populate_existing=True)
    )
    return effective_memory_mode(conversation.memory_mode, setting.memory_mode if setting is not None else None)
