"""Asynchronous Jev client with reservation-first delivery and safe bypass.

Provider I/O lives here; all decision validation remains in
``app.domain.routing``.  The client never caches results and never owns a
spending ledger: callers inject their reservation callback and durable request
identity, so another component can own caching/claims.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final

import httpx

from app.domain.routing import (
    JEV_MODEL,
    ROUTING_POLICY_VERSION,
    ClassificationError,
    InstructionTooLong,
    ReservationRequest,
    RoutingOutcome,
    build_classification_state,
    build_systemone_request,
    validate_classification_payload,
)

JEV_ENDPOINT: Final = "https://api.typesafe.ai/v1/systemone"
DEFAULT_DEADLINE_SECONDS: Final = 0.75
DEFAULT_FAILURE_THRESHOLD: Final = 3
DEFAULT_BYPASS_SECONDS: Final = 300.0
# Jev 1.14 costs $0.042 per million input tokens.  One micro-dollar is 1e-6 USD,
# so this is a conservative 0.042 micro-USD per estimated input token.
MICRO_USD_PER_INPUT_TOKEN: Final = 0.042

ReservationCallback = Callable[[ReservationRequest], Awaitable[bool]]


class JevCircuitBreaker:
    """Three consecutive operational failures open a five-minute bypass window.

    Only provider/validation failures call ``record_failure``.  Local conditions
    (missing key, reservation denial, oversized instruction) neither send a
    request nor count.  Any valid vendor response, including a low-confidence
    decision, calls ``record_success`` and closes the counter.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        bypass_seconds: float = DEFAULT_BYPASS_SECONDS,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if bypass_seconds <= 0:
            raise ValueError("bypass_seconds must be positive")
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._bypass_seconds = bypass_seconds
        self._consecutive_failures = 0
        self._open_until: float | None = None

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def open_until(self) -> float | None:
        return self._open_until

    def is_open(self) -> bool:
        return self._open_until is not None and self._clock() < self._open_until

    def record_failure(self) -> None:
        if self.is_open():
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._open_until = self._clock() + self._bypass_seconds
            self._consecutive_failures = 0

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = None


def _classification_state(instruction: str, context: Sequence[str]):
    try:
        return build_classification_state(instruction, context), None
    except InstructionTooLong:
        return None, "INSTRUCTION_TOO_LONG"
    except ValueError:
        return None, "INVALID_INSTRUCTION"


class JevClient:
    """One-request, zero-retry classifier for a single turn.

    ``reserve`` is mandatory.  If it is missing, construction fails rather than
    allowing an unreserved live request.  ``transport`` is an
    ``httpx.AsyncBaseTransport`` so tests can use ``httpx.MockTransport`` and the
    benchmark can inject a recorded transport.
    """

    def __init__(
        self,
        api_key: str,
        reserve: ReservationCallback,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
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
        self._clock = clock
        self._deadline_seconds = deadline_seconds
        self._circuit = circuit or JevCircuitBreaker(clock=clock)
        self._endpoint = endpoint

    @property
    def circuit(self) -> JevCircuitBreaker:
        return self._circuit

    async def classify(
        self,
        *,
        request_id: str,
        turn_id: str,
        instruction: str,
        context: Sequence[str] = (),
        policy_version: str = ROUTING_POLICY_VERSION,
    ) -> RoutingOutcome:
        """Classify one finalized turn within the total decision deadline."""
        started = self._clock()

        guard = self._guard(request_id, turn_id, started)
        if guard is not None:
            return guard
        state, error = _classification_state(instruction, context)
        if error is not None:
            return self._bypass(request_id, turn_id, error, started)

        payload = build_systemone_request(state)
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        estimated_tokens = max(1, math.ceil(len(serialized) / 4))
        reservation = ReservationRequest(
            request_id=request_id,
            provider="jev",
            model=JEV_MODEL,
            estimated_input_tokens=estimated_tokens,
            estimated_output_tokens=0,
            estimated_cost_micro_usd=max(1, math.ceil(estimated_tokens * MICRO_USD_PER_INPUT_TOKEN)),
            purpose="classification",
        )

        phase = "reservation"
        try:
            async with asyncio.timeout(self._deadline_seconds):
                try:
                    permitted = await self._reserve(reservation)
                except Exception:
                    permitted = False
                if not permitted:
                    return self._bypass(request_id, turn_id, "RESERVATION_DENIED", started)
                phase = "network"
                response = await self._post(payload)
        except TimeoutError:
            if phase == "reservation":
                return self._bypass(request_id, turn_id, "RESERVATION_DENIED", started)
            return self._failure(request_id, turn_id, "TIMEOUT", started)
        except httpx.HTTPError:
            return self._failure(request_id, turn_id, "PROVIDER_FAILURE", started)

        failure = self._status_failure(response)
        if failure is not None:
            return self._failure(request_id, turn_id, failure, started)

        try:
            body = response.json()
        except ValueError:
            return self._failure(request_id, turn_id, "INVALID_RESPONSE", started)

        try:
            outcome = validate_classification_payload(
                body,
                request_id=request_id,
                turn_id=turn_id,
                policy_version=policy_version,
            )
        except ClassificationError as exc:
            code = "MODEL_MISMATCH" if exc.code == "MODEL_MISMATCH" else "INVALID_RESPONSE"
            return self._failure(request_id, turn_id, code, started)

        self._circuit.record_success()
        return outcome.model_copy(update={"elapsed_ms": self._elapsed(started)})

    def _guard(self, request_id: str, turn_id: str, started: float) -> RoutingOutcome | None:
        if not self._api_key.strip():
            return self._bypass(request_id, turn_id, "MISSING_CREDENTIALS", started)
        if self._circuit.is_open():
            return self._bypass(request_id, turn_id, "CIRCUIT_OPEN", started)
        return None

    async def _post(self, payload: Mapping[str, object]) -> httpx.Response:
        transport = self._transport
        client_transport = transport if transport is not None else httpx.AsyncHTTPTransport()
        timeout = httpx.Timeout(self._deadline_seconds)
        async with httpx.AsyncClient(
            transport=client_transport,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            return await client.post(
                self._endpoint,
                headers={
                    "Authorization": "Bearer " + self._api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

    @staticmethod
    def _status_failure(response: httpx.Response) -> str | None:
        status = response.status_code
        if status == 200:
            return None
        text = ""
        with contextlib.suppress(Exception):
            text = response.text.casefold()
        if status == 402 or any(
            marker in text
            for marker in ("insufficient credit", "credit balance", "credit exhausted", "insufficient_quota", "billing")
        ):
            return "CREDIT_EXHAUSTED"
        return "PROVIDER_FAILURE"

    def _elapsed(self, started: float) -> float:
        return round(max(0.0, self._clock() - started) * 1000, 3)

    def _bypass(self, request_id: str, turn_id: str, reason: str, started: float) -> RoutingOutcome:
        return RoutingOutcome(
            kind="bypass",
            request_id=request_id,
            turn_id=turn_id,
            reason=reason,
            elapsed_ms=self._elapsed(started),
        )

    def _failure(self, request_id: str, turn_id: str, reason: str, started: float) -> RoutingOutcome:
        self._circuit.record_failure()
        return self._bypass(request_id, turn_id, reason, started)


__all__ = [
    "DEFAULT_BYPASS_SECONDS",
    "DEFAULT_DEADLINE_SECONDS",
    "JEV_ENDPOINT",
    "JevCircuitBreaker",
    "JevClient",
    "ReservationCallback",
]
