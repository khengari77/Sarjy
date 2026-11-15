"""Jev UI-command classifier: one request, validated immutable result, never raises.

Reuses the routing client's circuit breaker and guard semantics.  Every failure
(missing key, circuit open, reservation denial, timeout, transport error, bad
status, malformed body) returns ``None`` so the turn proceeds to the main model.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Final, Protocol

import httpx

from app.domain.routing import JEV_MODEL, ReservationRequest
from app.domain.ui_routing import UiClassification, build_ui_request, parse_ui_response
from app.services.jev import (
    JEV_ENDPOINT,
    JevCircuitBreaker,
    ReservationCallback,
)

logger = logging.getLogger(__name__)

# Fixed prompt plus a short instruction; a conservative estimate for the audit record.
_ESTIMATED_INPUT_TOKENS: Final = 900

# Seven questions per request run slower than the routing call (live p90 ~700-830 ms, outliers 2-3 s).
UI_DEADLINE_SECONDS: Final = 1.2


class UiClassifier(Protocol):
    async def classify(self, *, request_id: str, instruction: str) -> UiClassification | None: ...


class JevUiClient:
    def __init__(
        self,
        api_key: str,
        reserve: ReservationCallback,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float = UI_DEADLINE_SECONDS,
        circuit: JevCircuitBreaker | None = None,
        endpoint: str = JEV_ENDPOINT,
    ):
        if not callable(reserve):
            raise TypeError("reserve must be an injected async callback")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        self._api_key = api_key or ""
        self._reserve = reserve
        self._transport = transport
        self._deadline = deadline_seconds
        self._circuit = circuit or JevCircuitBreaker(clock=clock)
        self._endpoint = endpoint

    @property
    def circuit(self) -> JevCircuitBreaker:
        return self._circuit

    async def classify(self, *, request_id: str, instruction: str) -> UiClassification | None:
        if not self._api_key.strip() or not instruction.strip():
            return None
        if self._circuit.is_open():
            return None
        reservation = ReservationRequest(
            request_id=request_id[:100] or "ui",
            provider="jev",
            model=JEV_MODEL,
            estimated_input_tokens=_ESTIMATED_INPUT_TOKENS,
            estimated_cost_micro_usd=1,
        )
        phase = "reservation"
        try:
            async with asyncio.timeout(self._deadline):
                try:
                    permitted = await self._reserve(reservation)
                except Exception:
                    permitted = False
                if not permitted:
                    return None
                phase = "network"
                response = await self._post(instruction)
        except TimeoutError:
            if phase == "network":
                self._circuit.record_failure()
            logger.warning("jev ui classification failed: %s", "TIMEOUT" if phase == "network" else "RESERVATION")
            return None
        except httpx.HTTPError:
            self._circuit.record_failure()
            logger.warning("jev ui classification failed: PROVIDER_FAILURE")
            return None
        if response.status_code != 200:
            self._circuit.record_failure()
            logger.warning("jev ui classification failed: HTTP_%s", response.status_code)
            return None
        try:
            result = parse_ui_response(response.json())
        except ValueError:
            result = None
        if result is None:
            self._circuit.record_failure()
            logger.warning("jev ui classification failed: INVALID_RESPONSE")
            return None
        self._circuit.record_success()
        return result

    async def _post(self, instruction: str) -> httpx.Response:
        transport = self._transport if self._transport is not None else httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(self._deadline), follow_redirects=False
        ) as client:
            return await client.post(
                self._endpoint,
                headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                json=build_ui_request(instruction),
            )


__all__ = ["UI_DEADLINE_SECONDS", "JevUiClient", "UiClassifier"]
