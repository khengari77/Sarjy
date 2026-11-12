"""Keyword retrieval and operation history share the production exclusion policy."""
import asyncio

from app.infrastructure.database import ContextExclusion, ContextOperation, Memory, now
from app.services.context_operations import keyword_search, list_operations
from test_memory import setup


def test_keyword_search_excludes_corrected_descendants(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / 'lineage.db')
        try:
            async with sessions() as db:
                old = Memory(id='old', owner_id='alice', content='Trip to Tripoli',
                    source_conversation_id='a1', version=1, status='active')
                db.add(old)
                await db.flush()
                new = Memory(id='new', owner_id='alice', content='Trip to London',
                    source_conversation_id='a1', version=2, status='active', supersedes_id='old')
                db.add(new)
                db.add(ContextOperation(id='op', owner_id='alice', conversation_id='a1',
                    command_type='exclude', idempotency_key='op-key', outcome='applied',
                    payload={}, created_at=now()))
                await db.flush()
                db.add(ContextExclusion(id='ex', owner_id='alice', conversation_id='a1',
                    memory_id='old', operation_id='op', active=True, created_at=now()))
                await db.commit()
            async with sessions() as db:
                hits = await keyword_search(db, owner_id='alice', conversation_id='a1', query='trip', limit=10)
                assert hits == ()
        finally:
            await engine.dispose()
    asyncio.run(run())
