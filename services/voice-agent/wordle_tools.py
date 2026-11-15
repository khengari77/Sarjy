"""Model-facing Wordle function tools, mixed into the Sarjy agent.

Like the weather tool, the model decides when to call them. Each call posts to
``/internal/agent/tool/wordle-*`` (the server owns the game and the answer) and
then tells the web client to open or refresh the popup over ``sarjy.wordle``.
The data message carries no game state: the client refetches the authoritative
board, so the answer can never leak through it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from app.domain.tool_operations import operation_key
from livekit.agents import RunContext, function_tool

WORDLE_FAILED = "The Wordle tool failed. Tell the user you could not do that right now; do not guess the outcome."


async def publish_wordle(room, action: str) -> None:
    """Tell the web client to open, refresh or close the Wordle popup."""
    await room.local_participant.publish_data(
        json.dumps({"action": action}).encode(), reliable=True, topic="sarjy.wordle"
    )


class WordleTools:
    backend: httpx.AsyncClient
    current_turn: str | None
    publish_wordle: Any

    async def _wordle_tool(self, path: str, ctx: RunContext, **arguments: str) -> str:
        if not self.current_turn:
            raise ValueError("TOOL_TURN_REQUIRED")
        call_id = getattr(ctx.function_call, "call_id", "")
        try:
            response = await self.backend.post(
                f"/internal/agent/tool/{path}",
                json={
                    **arguments,
                    "turn_id": self.current_turn,
                    "request_id": operation_key(self.current_turn, call_id),
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return WORDLE_FAILED
        if not isinstance(body, dict):
            return WORDLE_FAILED
        ui = body.get("ui")
        if self.publish_wordle is not None and ui in {"open", "refresh", "close"}:
            try:
                await self.publish_wordle(ui)
            except Exception:
                return f"{body.get('message', '')} (The popup could not be updated; the game state is saved.)"
        return str(body.get("message") or WORDLE_FAILED)

    @function_tool()
    async def play_wordle(self, ctx: RunContext):
        """Start or resume the user's daily Wordle and show the Wordle popup. Use whenever the user wants to play Wordle, in any phrasing (for example "can we play the daily Wordle?")."""
        return await self._wordle_tool("wordle-play", ctx)

    @function_tool()
    async def submit_wordle_guess(self, ctx: RunContext, word: str):
        """Submit one five-letter guess in the user's open Wordle game; the popup board updates live. Pass the letters the user said as one word, for example CRANE. Report only what the tool result says."""
        return await self._wordle_tool("wordle-guess", ctx, word=word)

    @function_tool()
    async def wordle_status(self, ctx: RunContext):
        """Read the user's Wordle status: guesses made with their letter feedback and guesses left. Never states the answer."""
        return await self._wordle_tool("wordle-status", ctx)

    @function_tool()
    async def close_wordle(self, ctx: RunContext):
        """Close the Wordle popup when the user wants to stop playing or go back to chat. The game stays saved."""
        return await self._wordle_tool("wordle-close", ctx)
