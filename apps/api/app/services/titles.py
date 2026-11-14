"""Bounded, best-effort conversation-title generation.

A conversation keeps the literal sentinel title until its first exchange is
persisted.  Generation is deliberately side-effect-free with respect to the
reply path: it runs after the finalizing turn is committed, never raises into
the caller and only writes while the conversation still carries the sentinel.

The sentinel check is repeated in the ``UPDATE`` predicate, so a rename that
lands while the provider call is in flight always wins.
"""

from __future__ import annotations

import asyncio
import re
from typing import Final

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.budget import DEEPSEEK_DAILY_MICRO_USD, DEEPSEEK_REQUEST_MICRO_USD
from app.infrastructure.database import Conversation, Turn
from app.services.budget import reserve

SENTINEL_TITLE: Final = "New conversation"
TITLE_MODEL: Final = "deepseek-flash"
TITLE_URL: Final = "https://api.deepseek.com/v1/chat/completions"
MAX_SOURCE_CHARS: Final = 600
MAX_TITLE_CHARS: Final = 60
TITLE_TIMEOUT_SECONDS: Final = 4.0

_MARKDOWN = re.compile(r"[*_#~`]+")
_EDGE_QUOTES = "\"'“”‘’`«»"
_TRAILING_PERIOD = "."
_ARABIC = re.compile(r"[\u0600-\u06FF]")
_LATIN = re.compile(r"[A-Za-z]")

_SYSTEM_PROMPT = (
    "Write a conversation title of 3 to 6 words in {language}. "
    "Return only the title, without quotes, markdown or a trailing period."
)


def _language_for(user_text: str) -> str:
    arabic = len(_ARABIC.findall(user_text))
    latin = len(_LATIN.findall(user_text))
    return "Arabic" if arabic > latin else "English"


def _truncate(text: str, limit: int = MAX_SOURCE_CHARS) -> str:
    return text[:limit]


def build_title_messages(user_text: str, assistant_text: str) -> list[dict[str, str]]:
    """The exact messages sent to the provider; each source is bounded."""
    language = _language_for(user_text)
    content = f"User: {_truncate(user_text)}\nAssistant: {_truncate(assistant_text)}"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT.format(language=language)},
        {"role": "user", "content": content},
    ]


def sanitize_title(raw: object) -> str | None:
    """Normalize one provider title, or ``None`` when it must not be written."""
    if not isinstance(raw, str):
        return None
    text = _MARKDOWN.sub("", raw)
    text = " ".join(text.split())
    text = text.strip(_EDGE_QUOTES).strip()
    if text.endswith(_TRAILING_PERIOD):
        text = text[:-1].rstrip()
    text = text[:MAX_TITLE_CHARS].strip().strip(_EDGE_QUOTES).rstrip(_TRAILING_PERIOD).strip()
    if not text or text == SENTINEL_TITLE:
        return None
    return text


def _message_content(payload: object) -> object:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    return message.get("content")


async def fetch_title(
    user_text: str,
    assistant_text: str,
    *,
    api_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = TITLE_TIMEOUT_SECONDS,
) -> str | None:
    """Ask the provider once; any failure is an ordinary ``None``."""
    if not api_key.strip():
        return None
    payload = {
        "model": TITLE_MODEL,
        "messages": build_title_messages(user_text, assistant_text),
        "temperature": 0,
        "max_tokens": 24,
        "stream": False,
    }
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                response = await client.post(
                    TITLE_URL,
                    headers={"authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
    except (TimeoutError, httpx.HTTPError, ValueError):
        return None
    return sanitize_title(_message_content(data))


async def _first_turn(db: AsyncSession, conversation_id: str, role: str) -> Turn | None:
    return await db.scalar(
        select(Turn)
        .where(Turn.conversation_id == conversation_id, Turn.role == role)
        .order_by(Turn.created_at, Turn.id)
        .limit(1)
    )


async def _title_sources(sessions: async_sessionmaker[AsyncSession], conversation_id: str) -> tuple[str, str] | None:
    """The first exchange's text, or ``None`` when it is not ready or not ours."""
    async with sessions() as db:
        conversation = await db.get(Conversation, conversation_id)
        # A user rename is authoritative forever: no provider call, no write.
        if conversation is None or conversation.title != SENTINEL_TITLE:
            return None
        user_turn = await _first_turn(db, conversation_id, "user")
        assistant_turn = await _first_turn(db, conversation_id, "assistant")
        if user_turn is None or assistant_turn is None:
            return None
        return user_turn.text, assistant_turn.text


async def generate_conversation_title(
    sessions: async_sessionmaker[AsyncSession],
    conversation_id: str,
    *,
    api_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
    reserve_budget: bool = True,
    budget_units: int = DEEPSEEK_REQUEST_MICRO_USD,
    budget_limit: int = DEEPSEEK_DAILY_MICRO_USD,
    timeout: float = TITLE_TIMEOUT_SECONDS,
) -> str | None:
    """Generate and store one title, never raising at the background boundary."""
    try:
        sources = await _title_sources(sessions, conversation_id)
        if sources is None:
            return None
        if reserve_budget:
            try:
                async with sessions() as db:
                    await reserve(db, "deepseek", units=budget_units, limit=budget_limit)
            except ValueError:
                return None
        title = await fetch_title(
            sources[0],
            sources[1],
            api_key=api_key,
            transport=transport,
            timeout=timeout,
        )
        if title is None:
            return None
        # No connection is held across the provider call; the sentinel predicate
        # is the final authority against a rename that landed meanwhile.
        async with sessions() as db:
            changed = await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id, Conversation.title == SENTINEL_TITLE)
                .values(title=title)
            )
            await db.commit()
            return title if changed.rowcount == 1 else None
    except Exception:  # noqa: BLE001 - fire-and-forget boundary must never escape
        return None
