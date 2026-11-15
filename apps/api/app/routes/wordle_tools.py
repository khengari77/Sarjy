"""Model-facing Wordle tool routes (play, guess, status, close).

The tools reuse the Wordle services for the conversation owner and keep the
voice-turn tool-operation ledger, so a retried call replays one result.
"""

from fastapi import Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.wordle import WordleSource
from app.services.wordle_tools import wordle_tool


class WordleToolInput(BaseModel):
    turn_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
    word: str | None = Field(default=None, max_length=40)


ROUTES = {
    "wordle-play": "play",
    "wordle-guess": "guess",
    "wordle-status": "status",
    "wordle-close": "close",
    "wordle-reset": "reset",
}


def register_wordle_tool_routes(app, *, config: Settings, database, run_tool, source: WordleSource):
    def make(path: str, action: str):
        @app.post(f"/internal/agent/tool/{path}", name=f"agent_{action}_wordle")
        async def route(body: WordleToolInput, request: Request, db: AsyncSession = Depends(database)):
            async def effect(owner_id, _conversation_id):
                return await wordle_tool(
                    db,
                    action=action,
                    owner_id=owner_id,
                    enabled=config.wordle_enabled,
                    source=source,
                    timezone=config.wordle_timezone,
                    word=body.word,
                    request_id=body.request_id,
                )

            return await run_tool(
                db, request, body, f"wordle_{action}", {"word": body.word} if action == "guess" else {}, effect
            )

    for path, action in ROUTES.items():
        make(path, action)
