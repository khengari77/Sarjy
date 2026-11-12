import asyncio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.tool_operations import (
    ToolOperationState,
    ToolReplayKind,
    operation_key,
    replay_kind,
    terminal_state,
)
from app.infrastructure.database import Base, Conversation, Identity, Turn
from app.services.tool_operations import (
    ToolOperationError,
    claim_tool_operation,
    complete_tool_operation,
    fail_tool_operation,
    unknown_tool_operation,
)


def test_pure_identity_and_terminal_mapping():
    assert operation_key('turn', 'call') == 'turn:call'
    long_key = operation_key('t' * 100, 'c' * 100)
    assert len(long_key) <= 100 and long_key.startswith('op:')
    assert terminal_state('WEATHER_UNAVAILABLE') is ToolOperationState.UNKNOWN
    assert terminal_state('LOCATION_NOT_FOUND') is ToolOperationState.FAILED
    assert terminal_state('SOMETHING_NEW') is ToolOperationState.UNKNOWN
    assert replay_kind('completed') is ToolReplayKind.RESULT
    assert replay_kind('pending') is ToolReplayKind.IN_PROGRESS
    assert replay_kind('failed') is ToolReplayKind.FAILED
    assert replay_kind('unknown') is ToolReplayKind.UNKNOWN
    assert replay_kind('garbage') is ToolReplayKind.UNKNOWN


async def setup(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/tools.db')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Identity(id='owner', token_hash='hash', expires_at='2030'))
        db.add(Conversation(id='chat', owner_id='owner'))
        db.add(Turn(id='turn', conversation_id='chat', request_id='request', role='user', text='Weather?'))
        db.add(Turn(id='other', conversation_id='chat', request_id='request-2', role='user', text='Weather?'))
        await db.commit()
    return engine, sessions


def test_claim_complete_and_replay_are_durable(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path)
        try:
            async with sessions() as db:
                claim = await claim_tool_operation(db, turn_id='turn', operation_key='turn:call', name='weather')
                assert claim.kind == 'claimed'
                await db.commit()
            async with sessions() as db:
                pending = await claim_tool_operation(db, turn_id='turn', operation_key='turn:call', name='weather')
                assert pending.kind == 'pending'
                await complete_tool_operation(db, operation_key='turn:call', result={'city': 'Tripoli'})
                await db.commit()
            async with sessions() as db:
                replay = await claim_tool_operation(db, turn_id='turn', operation_key='turn:call', name='weather')
                assert replay.kind == 'completed'
                assert replay.result == {'city': 'Tripoli'}
                other = await claim_tool_operation(db, turn_id='turn', operation_key='turn:other', name='weather')
                assert other.kind == 'claimed'
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_failed_and_unknown_are_terminal_and_turn_scoped(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path)
        try:
            async with sessions() as db:
                await claim_tool_operation(db, turn_id='turn', operation_key='turn:failed', name='weather')
                await fail_tool_operation(db, operation_key='turn:failed', error_code='LOCATION_NOT_FOUND')
                await claim_tool_operation(db, turn_id='turn', operation_key='turn:unknown', name='weather')
                await unknown_tool_operation(db, operation_key='turn:unknown', error_code='WEATHER_UNAVAILABLE')
                await db.commit()
            async with sessions() as db:
                failed = await claim_tool_operation(db, turn_id='turn', operation_key='turn:failed', name='weather')
                assert failed.kind == 'failed'
                assert failed.result == {'error': 'LOCATION_NOT_FOUND'}
                unknown = await claim_tool_operation(db, turn_id='turn', operation_key='turn:unknown', name='weather')
                assert unknown.kind == 'unknown'
                with pytest.raises(ToolOperationError) as error:
                    await claim_tool_operation(db, turn_id='other', operation_key='turn:unknown', name='weather')
                assert error.value.code == 'TOOL_OPERATION_CONFLICT'
        finally:
            await engine.dispose()

    asyncio.run(run())
