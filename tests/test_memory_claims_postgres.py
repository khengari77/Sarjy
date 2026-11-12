"""Real PostgreSQL claim identity and independent delivery acceptance."""
import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.database import Conversation, DevelopmentBudget, Identity, MemoryWriteRequest
from app.services.memory import MemoryService, OpenAIEmbedder


def test_postgres_duplicate_request_has_one_reservation_and_one_delivery():
    url = os.environ.get('TEST_MEMORY_POSTGRES_URL')
    if not url:
        pytest.skip('TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL')
    async def run():
        engine=create_async_engine(url);sessions=async_sessionmaker(engine,expire_on_commit=False)
        owner,chat,key=(str(uuid4()) for _ in range(3));started,release=asyncio.Event(),asyncio.Event();calls=[]
        async def transport(request):
            calls.append(request);started.set();await release.wait()
            return httpx.Response(200,json={'data':[{'embedding':[1.0]+[0.0]*1535}]})
        adapter=OpenAIEmbedder('test-only',transport=httpx.MockTransport(transport))
        async def write():
            async with sessions() as db:
                return await MemoryService(db,adapter).create_memory(owner,chat,None,'Fact',key)
        try:
            async with sessions() as db:
                db.add(Identity(id=owner,token_hash=owner,expires_at='2030'));await db.commit()
                db.add(Conversation(id=chat,owner_id=owner));await db.commit()
                before=await db.scalar(select(DevelopmentBudget.reserved_units).where(DevelopmentBudget.provider=='openai')) or 0
            first=asyncio.create_task(write());await asyncio.wait_for(started.wait(),3)
            with pytest.raises(HTTPException) as failure:
                await write()
            assert failure.value.detail['code']=='MEMORY_REQUEST_IN_PROGRESS'
            release.set();result=await first
            assert await write()==result
            assert len(calls)==1
            async with sessions() as db:
                after=await db.scalar(select(DevelopmentBudget.reserved_units).where(DevelopmentBudget.provider=='openai'))
                assert after-before==20000
                request=await db.scalar(select(MemoryWriteRequest).where(MemoryWriteRequest.owner_id==owner))
                assert request.state=='completed'
        finally:release.set();await engine.dispose()
    asyncio.run(run())
