"""Retryable room cleanup. Provider absence is evidence only at observation time."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

from aiohttp import ClientTimeout
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings
from sqlalchemy import select, update
from app.infrastructure.database import VoiceSession

CLEANUP_TIMEOUT_SECONDS = 10

async def cleanup_room(db: AsyncSession, voice: VoiceSession, settings: Settings, *, now: datetime | None = None) -> Literal['already_complete', 'complete', 'pending']:
    now = now or datetime.now(timezone.utc)
    if voice.status not in {'ended', 'failed'}:
        raise ValueError('SESSION_NOT_TERMINAL')
    if voice.room_cleanup_completed_at:
        return 'already_complete'
    # End read transactions before any network wait.
    identifier, room, status, expiry = voice.id, voice.room_name, voice.status, voice.expires_at
    await db.commit()
    if not all((settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)):
        raise ValueError('ROOM_CLEANUP_NOT_CONFIGURED')
    from livekit import api
    try:
        async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
            async with api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret, timeout=ClientTimeout(total=CLEANUP_TIMEOUT_SECONDS)) as lk:
                await lk.room.delete_room(api.DeleteRoomRequest(room=room))
    except api.TwirpError as exc:
        if exc.code != 'not_found':
            logging.warning('Room cleanup pending', extra={'session_id':identifier, 'error_code':'ROOM_CLEANUP_FAILED'})
            raise ValueError('ROOM_CLEANUP_FAILED') from exc
    except Exception as exc:
        logging.warning('Room cleanup pending', extra={'session_id':identifier, 'error_code':'ROOM_CLEANUP_FAILED'})
        raise ValueError('ROOM_CLEANUP_FAILED') from exc
    # A timed-out dispatch can materialize after an early delete/not-found.
    if status == 'failed' and datetime.fromisoformat(expiry) > now:
        return 'pending'
    await db.execute(update(VoiceSession).where(VoiceSession.id == identifier, VoiceSession.room_cleanup_completed_at.is_(None)).values(room_cleanup_completed_at=now.isoformat()))
    await db.commit()
    return 'complete'

async def reconcile(db: AsyncSession, settings: Settings, *, apply: bool = False, limit: int = 20) -> dict[str, int | bool]:
    if not 1 <= limit <= 100:
        raise ValueError('INVALID_BATCH_LIMIT')
    ids = list(await db.scalars(select(VoiceSession.id).where(VoiceSession.status.in_(['ended','failed']), VoiceSession.room_cleanup_completed_at.is_(None)).order_by(VoiceSession.expires_at, VoiceSession.id).limit(limit)))
    await db.commit()
    result = {'selected':len(ids), 'completed':0, 'pending':0, 'errors':0, 'dry_run':not apply}
    for identifier in ids:
        if not apply:
            result['pending'] += 1
            continue
        voice = await db.get(VoiceSession, identifier)
        if not voice or voice.status not in {'ended','failed'}:
            await db.commit()
            continue
        try:
            outcome = await cleanup_room(db, voice, settings)
            result['pending' if outcome == 'pending' else 'completed'] += 1
        except ValueError:
            await db.rollback()
            result['errors'] += 1
    return result
