import asyncio
from datetime import datetime, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.infrastructure.database import Base, Identity, Conversation, Memory, DevelopmentBudget, ContextOperation, ContextSnapshot, Turn
from app.services.authorization import owned_conversation, owned_memory, owned_operation, owned_snapshot
from app.services.budget import guarded_openai_request
from app.domain.budget import budget_day, OPENAI_DAILY_MICRO_USD


def test_owner_scoped_reads_and_budget_denial_precede_external_effects(tmp_path):
    async def run():
        engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/boundaries.db')
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                db.add_all([Identity(id='alice',token_hash='a',expires_at='2030-01-01'),Identity(id='bob',token_hash='b',expires_at='2030-01-01')])
                await db.commit()
                db.add(Conversation(id='chat',owner_id='alice',title='test',revision=0))
                await db.commit()
                db.add(Memory(id='memory',owner_id='alice',content='Tripoli',source_conversation_id='chat',version=1,status='active'))
                await db.commit()
                assert (await owned_conversation(db,'alice','chat')).id == 'chat'
                assert (await owned_memory(db,'alice','memory')).id == 'memory'
                for lookup, identifier in [(owned_conversation,'chat'),(owned_memory,'memory')]:
                    for owner in ['bob','unknown']:
                        with pytest.raises(HTTPException) as error:
                            await lookup(db,owner,identifier)
                        assert error.value.status_code == 404
                    with pytest.raises(HTTPException):
                        await lookup(db,'alice','missing')
                db.add(DevelopmentBudget(day=budget_day(datetime.now(timezone.utc)),provider='openai',reserved_units=OPENAI_DAILY_MICRO_USD))
                await db.commit()
                effects=[]
                async def external():
                    effects.append('called')
                with pytest.raises(ValueError,match='DAILY_BUDGET_EXHAUSTED'):
                    await guarded_openai_request(db,external)
                assert effects == []
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_guard_reserves_before_delivery_and_retains_uncertain_spending(tmp_path):
    from sqlalchemy import select
    from app.domain.budget import OPENAI_REQUEST_MICRO_USD
    async def run():
        engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/delivery.db')
        factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                async def external():
                    total=await db.scalar(select(DevelopmentBudget.reserved_units))
                    assert total == OPENAI_REQUEST_MICRO_USD
                    raise RuntimeError('delivery uncertain')
                with pytest.raises(RuntimeError,match='delivery uncertain'):
                    await guarded_openai_request(db,external)
            async with factory() as db:
                assert await db.scalar(select(DevelopmentBudget.reserved_units)) == OPENAI_REQUEST_MICRO_USD
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_operation_and_snapshot_owner_isolation(tmp_path):
    async def run():
        engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/resources.db')
        factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                db.add_all([Identity(id='alice',token_hash='a',expires_at='2030-01-01'),Identity(id='bob',token_hash='b',expires_at='2030-01-01')])
                await db.commit()
                db.add_all([Conversation(id='chat',owner_id='alice',title='Alice',revision=0),Conversation(id='bob-chat',owner_id='bob',title='Bob',revision=0),Conversation(id='other',owner_id='alice',title='Other',revision=0)])
                await db.commit()
                db.add(Turn(id='turn',conversation_id='chat',request_id='request',role='user',text='Fact',status='completed'))
                db.add(ContextOperation(id='operation',owner_id='alice',conversation_id='chat',command_type='exclude',payload={},idempotency_key='operation-key',outcome='completed'))
                await db.commit()
                db.add(ContextSnapshot(id='snapshot',turn_id='turn',revision=0,canonical_messages=[],manifest={},pipeline_config={}))
                await db.commit()
                assert (await owned_operation(db,'alice','operation',conversation_id='chat')).id=='operation'
                assert (await owned_snapshot(db,'alice','turn')).id=='snapshot'
                for owner,conversation in [('bob','chat'),('bob','bob-chat'),('alice','other'),('alice','missing')]:
                    with pytest.raises(HTTPException) as error:
                        await owned_operation(db,owner,'operation',conversation_id=conversation)
                    assert error.value.status_code==404
                with pytest.raises(HTTPException):
                    await owned_operation(db,'alice','missing',conversation_id='chat')
                for owner,turn in [('bob','turn'),('unknown','turn'),('alice','missing')]:
                    with pytest.raises(HTTPException) as error:
                        await owned_snapshot(db,owner,turn)
                    assert error.value.status_code==404
        finally:
            await engine.dispose()
    asyncio.run(run())
