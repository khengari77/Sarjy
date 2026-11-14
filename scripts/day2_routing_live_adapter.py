"""Live routing adapters for the Day 2 routing benchmark.

The benchmark harness loads this module explicitly:

    uv run python scripts/day2_routing_benchmark.py --live \
        --live-module scripts/day2_routing_live_adapter.py

``build_routers(fixture, cache)`` returns the local/exact router, the
Jev-assisted router and the direct primary-classifier router.  It does not own a
spending ledger: reservations are approved in memory by :class:`ReservationBudget`
for the duration of one run only.  Provider credentials come from the
environment and are never printed, persisted or included in a result.

Required environment:

``JEV_API_KEY``
    System One / Jev credential.
``DEEPSEEK_API_KEY``
    DeepSeek credential for extraction and the direct router.

Optional environment:

``SARJY_ROUTING_LIVE_MAX_CALLS``
    Hard cap on approved provider calls for the whole run (default 96).
``SARJY_ROUTING_LIVE_MAX_TOKENS``
    Hard cap on estimated input plus output tokens for the whole run
    (default 250000).
``SARJY_ROUTING_EXTRACTION_INPUT_PRICE`` / ``..._OUTPUT_PRICE``
    USD per million tokens used only for extraction reservation estimates.

A request that would exceed either cap is skipped and reported as
``RESERVATION_DENIED``; it is never treated as a successful result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

import day2_routing_benchmark as harness
import httpx
from app.domain.contracts import Intent, RestoreContextOperation, SelectApprovedVoice, parse_action
from app.domain.routing import (
    ACTION_INTENTS,
    COMPLEXITY_CRITERIA,
    INTENT_CRITERIA,
    ROUTING_POLICY_VERSION,
    TIER_OPTIONS,
    InstructionTooLong,
    ReservationRequest,
    RoutingOutcome,
    UsageMetadata,
    build_classification_state,
)
from app.services.action_extraction import (
    EXTRACTION_MODEL,
    ExtractionOutcome,
    GenerationConfig,
    GenerationRequest,
    GenerationResult,
    extract_action,
)
from app.services.jev import DEFAULT_DEADLINE_SECONDS, JevClient, ReservationCallback

DIRECT_MODEL: Final = "deepseek-flash"
DIRECT_ENDPOINT: Final = "https://api.deepseek.com/v1/chat/completions"
DIRECT_MAX_OUTPUT_TOKENS: Final = 256
DIRECT_TIMEOUT_SECONDS: Final = 10.0
EXTRACTION_MAX_OUTPUT_TOKENS: Final = 128
DEFAULT_MAX_CALLS: Final = 96
DEFAULT_MAX_TOKENS: Final = 250_000
AUTOMATIC_ROUTING_ENABLED: Final = False

_ACTION_EXAMPLES: Final[dict[str, str]] = {
    "AdjustSpeakingSpeed": '{"kind":"AdjustSpeakingSpeed","delta":-0.1}',
    "SetSpeakingSpeed": '{"kind":"SetSpeakingSpeed","speed":1.2}',
    "SelectApprovedVoice": '{"kind":"SelectApprovedVoice","voice_id":"<approved id>"}',
    "ShowConversationHistory": '{"kind":"ShowConversationHistory"}',
    "OpenMemoryExplorer": '{"kind":"OpenMemoryExplorer"}',
    "SetCaptionsVisible": '{"kind":"SetCaptionsVisible","visible":true}',
    "SearchMemory": '{"kind":"SearchMemory","query":"...","limit":20}',
    "ProposeContextExclusion": '{"kind":"ProposeContextExclusion","query":"...","keep":["..."],"exclude":["..."]}',
    "RestoreContextOperation": '{"kind":"RestoreContextOperation","operation_id":"<allowed id>"}',
}


class ConfigurationError(RuntimeError):
    """A live run was requested without the configuration it requires."""


class DeepSeekError(RuntimeError):
    """A bounded provider failure with a safe, non-content code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prompt_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required for a live routing run; export it or use offline modes")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive integer") from exc
    if value < 0:
        raise ConfigurationError(f"{name} must be a non-negative integer")
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ConfigurationError(f"{name} must be a non-negative finite number")
    return value


class ReservationBudget:
    """In-memory, per-run hard cap on approved calls and estimated tokens.

    This is deliberately not a spending ledger.  It enforces the benchmark's
    bounded run and records only denial reasons; nothing is persisted.
    """

    def __init__(self, *, max_calls: int, max_tokens: int):
        if max_calls < 0 or max_tokens < 0:
            raise ValueError("reservation caps must be non-negative")
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0
        self.denials: list[str] = []

    @classmethod
    def from_env(cls) -> ReservationBudget:
        return cls(
            max_calls=_env_int("SARJY_ROUTING_LIVE_MAX_CALLS", DEFAULT_MAX_CALLS),
            max_tokens=_env_int("SARJY_ROUTING_LIVE_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        )

    async def __call__(self, request: ReservationRequest) -> bool:
        estimated = request.estimated_input_tokens + request.estimated_output_tokens
        if self.calls + 1 > self.max_calls:
            self.denials.append("CALL_CAP")
            return False
        if self.tokens + estimated > self.max_tokens:
            self.denials.append("TOKEN_CAP")
            return False
        self.calls += 1
        self.tokens += estimated
        return True


def _credit_failure(response: httpx.Response) -> bool:
    if response.status_code == 402:
        return True
    try:
        text = response.text.casefold()
    except Exception:
        return False
    return any(
        marker in text
        for marker in ("insufficient credit", "credit balance", "credit exhausted", "insufficient_quota", "billing")
    )


class DeepSeekChat:
    """One bounded, zero-retry DeepSeek chat completion."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DIRECT_TIMEOUT_SECONDS,
    ):
        self._api_key = api_key or ""
        self._transport = transport
        self._timeout = timeout_seconds

    async def complete(self, prompt: str, *, max_tokens: int) -> tuple[str, UsageMetadata]:
        body = {
            "model": DIRECT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        transport = self._transport if self._transport is not None else httpx.AsyncHTTPTransport()
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    DIRECT_ENDPOINT,
                    headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise DeepSeekError("TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise DeepSeekError("PROVIDER_FAILURE") from exc

        if _credit_failure(response):
            raise DeepSeekError("CREDIT_EXHAUSTED")
        if response.status_code != 200:
            raise DeepSeekError("PROVIDER_FAILURE")
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError("INVALID_RESPONSE") from exc
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekError("INVALID_RESPONSE")
        return content, _usage(payload.get("usage"))


def _usage(raw: Any) -> UsageMetadata:
    if not isinstance(raw, Mapping):
        return UsageMetadata(input_tokens=0, output_tokens=0)
    input_tokens = raw.get("prompt_tokens", 0)
    output_tokens = raw.get("completion_tokens", 0)
    if isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens < 0:
        input_tokens = 0
    if isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or output_tokens < 0:
        output_tokens = 0
    return UsageMetadata(input_tokens=input_tokens, output_tokens=output_tokens)


def _parse_json_object(content: str) -> Mapping[str, Any] | None:
    text = content.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    try:
        payload = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            return None
    return payload if isinstance(payload, Mapping) else None


class LiveJevRouter(harness.JevAssistedRouter):
    """Exact first, then one Jev call and at most one bounded extraction."""

    provenance = "live"

    def __init__(
        self,
        fixture: harness.Fixture,
        *,
        api_key: str,
        deepseek_api_key: str,
        reserve: ReservationCallback,
        generation: GenerationConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        extraction_transport: httpx.AsyncBaseTransport | None = None,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ):
        self._reserve = reserve
        self._client = JevClient(api_key, reserve, transport=transport, deadline_seconds=deadline_seconds)
        self._deepseek = DeepSeekChat(deepseek_api_key, transport=extraction_transport)
        super().__init__(
            fixture,
            classify=self._classify,
            extract=self._extract,
            generation=generation,
        )

    async def _classify(self, request_id: str, turn_id: str, text: str) -> RoutingOutcome:
        return await self._client.classify(
            request_id=request_id,
            turn_id=turn_id,
            instruction=text,
            policy_version=ROUTING_POLICY_VERSION,
        )

    async def _extract(self, request_id: str, text: str) -> ExtractionOutcome:
        return await extract_action(
            text,
            request_id=request_id,
            reserve=self._reserve,
            generate=self._generate,
            generation=self.generation,
            approved_voice_ids=self.fixture.approved_voices,
            allowed_operation_ids=self.fixture.allowed_operation_ids,
        )

    async def _generate(self, request: GenerationRequest) -> GenerationResult:
        content, usage = await self._deepseek.complete(request.prompt, max_tokens=request.max_output_tokens)
        return GenerationResult(payload=content, usage=usage)


def _direct_prompt(case: harness.Case, fixture: harness.Fixture) -> str:
    state = build_classification_state(case.text)
    instructions = {
        "intents": INTENT_CRITERIA,
        "tiers": TIER_OPTIONS,
        "complexity": COMPLEXITY_CRITERIA,
        "actions": _ACTION_EXAMPLES,
        "approved_voice_ids": list(fixture.approved_voices),
        "allowed_operation_ids": list(fixture.allowed_operation_ids),
    }
    return (
        "Classify exactly one finalized English utterance and, only when it clearly "
        "requests one listed action, include that action. Return only one JSON object "
        'with keys "intent", "tier", "complexity" and "action". Set "action" to null '
        "when no listed action applies. Never invent an ID. Do not explain or add text.\n\n"
        "Allowed values and criteria:\n" + _canonical(instructions) + "\n\nDATA:\n" + _canonical(state)
    )


class DirectLiveRouter:
    """One DeepSeek primary classification per case, with a typed action."""

    id = "direct"
    model = DIRECT_MODEL
    provenance = "live"

    def __init__(
        self,
        fixture: harness.Fixture,
        *,
        api_key: str,
        reserve: ReservationCallback,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DIRECT_TIMEOUT_SECONDS,
    ):
        self.fixture = fixture
        self._reserve = reserve
        self._client = DeepSeekChat(api_key, transport=transport, timeout_seconds=timeout_seconds)

    def prompt_hash(self, case: harness.Case) -> str:
        return _prompt_hash({"router": self.id, "model": self.model, "prompt": _direct_prompt(case, self.fixture)})

    async def route(self, case: harness.Case) -> harness.RouterOutput:
        try:
            prompt = _direct_prompt(case, self.fixture)
        except InstructionTooLong:
            return harness.RouterOutput(error="INSTRUCTION_TOO_LONG")
        except ValueError:
            return harness.RouterOutput(error="INVALID_INSTRUCTION")

        estimated_input = max(1, math.ceil(len(prompt) / 4))
        estimated_cost = max(1, math.ceil(estimated_input * 0.042))
        reservation = ReservationRequest(
            request_id=f"bench:{case.id}",
            provider="deepseek",
            model=DIRECT_MODEL,
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=DIRECT_MAX_OUTPUT_TOKENS,
            estimated_cost_micro_usd=estimated_cost,
            purpose="classification",
        )
        try:
            permitted = await self._reserve(reservation)
        except Exception:
            permitted = False
        if not permitted:
            return harness.RouterOutput(reason="RESERVATION_DENIED", error="RESERVATION_DENIED")

        try:
            content, usage = await self._client.complete(prompt, max_tokens=DIRECT_MAX_OUTPUT_TOKENS)
        except DeepSeekError as exc:
            return harness.RouterOutput(requests=1, reason=exc.code, error=exc.code)

        usage_record = harness.UsageRecord(
            provider="deepseek",
            model=DIRECT_MODEL,
            purpose="direct",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        payload = _parse_json_object(content)
        if payload is None:
            return harness.RouterOutput(
                requests=1,
                usage=(usage_record,),
                reason="INVALID_RESPONSE",
                error="INVALID_RESPONSE",
                raw={"source": "direct_live"},
            )
        return _direct_output(payload, usage_record, self.fixture)


def _direct_output(
    payload: Mapping[str, Any],
    usage: harness.UsageRecord,
    fixture: harness.Fixture,
) -> harness.RouterOutput:
    intent_value = payload.get("intent")
    tier = payload.get("tier")
    complexity = payload.get("complexity")
    raw: dict[str, Any] = {
        "source": "direct_live",
        "intent": intent_value,
        "tier": tier,
        "complexity": complexity,
        "action": None,
    }
    if (
        not isinstance(intent_value, str)
        or intent_value not in {intent.value for intent in Intent}
        or isinstance(tier, bool)
        or not isinstance(tier, int)
        or not 1 <= tier <= 5
        or complexity not in {"simple", "complex"}
    ):
        return harness.RouterOutput(
            requests=1,
            usage=(usage,),
            reason="INVALID_DIRECT_PAYLOAD",
            error="INVALID_DIRECT_PAYLOAD",
            raw=raw,
        )

    action = None
    error = None
    action_payload = payload.get("action")
    if action_payload is not None:
        if not isinstance(action_payload, Mapping):
            error = "INVALID_DIRECT_ACTION"
        elif action_payload.get("kind") == "NoAction":
            action_payload = None
        else:
            try:
                action = parse_action(dict(action_payload))
            except (ValueError, TypeError, OverflowError):
                error = "INVALID_DIRECT_ACTION"

    if action is not None and not _references_allowed(action, fixture):
        action, error = None, "UNSUPPORTED_ACTION"
    if action is not None and error is None:
        raw["action"] = action.model_dump(mode="json")

    authorized = False
    if action is not None:
        authorized = type(action) in ACTION_INTENTS.get(Intent(intent_value), frozenset())
    return harness.RouterOutput(
        intent=intent_value,
        tier=tier,
        complexity=complexity,
        action=action.model_dump(mode="json") if action is not None else None,
        authorized=authorized,
        requests=1,
        usage=(usage,),
        reason=error,
        error=error,
        raw=raw,
    )


def _references_allowed(action: Any, fixture: harness.Fixture) -> bool:
    if isinstance(action, SelectApprovedVoice):
        return action.voice_id in set(fixture.approved_voices)
    if isinstance(action, RestoreContextOperation):
        return action.operation_id in set(fixture.allowed_operation_ids)
    return True


def _generation_config() -> GenerationConfig:
    return GenerationConfig(
        model=EXTRACTION_MODEL,
        max_output_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
        input_usd_per_million=_env_float("SARJY_ROUTING_EXTRACTION_INPUT_PRICE", 0.0),
        output_usd_per_million=_env_float("SARJY_ROUTING_EXTRACTION_OUTPUT_PRICE", 0.0),
    )


def build_routers(*, fixture: harness.Fixture, cache: harness.ResultCache) -> Sequence[harness.Router]:
    """Build the three benchmark routers with one shared per-run budget."""
    del cache
    jev_key = _require_env("JEV_API_KEY")
    deepseek_key = _require_env("DEEPSEEK_API_KEY")
    budget = ReservationBudget.from_env()
    generation = _generation_config()
    return [
        harness.LocalRouter(),
        LiveJevRouter(
            fixture,
            api_key=jev_key,
            deepseek_api_key=deepseek_key,
            reserve=budget,
            generation=generation,
        ),
        DirectLiveRouter(fixture, api_key=deepseek_key, reserve=budget),
    ]


__all__ = [
    "AUTOMATIC_ROUTING_ENABLED",
    "ConfigurationError",
    "DEFAULT_MAX_CALLS",
    "DEFAULT_MAX_TOKENS",
    "DIRECT_MODEL",
    "DeepSeekError",
    "DirectLiveRouter",
    "LiveJevRouter",
    "ReservationBudget",
    "build_routers",
]
