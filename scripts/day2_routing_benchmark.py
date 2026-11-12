"""Resumable Day 2 routing benchmark.

Default execution is an offline dry run: it validates the frozen 24-case fixture
and report structure and never contacts a provider.  ``--scripted`` replays
recorded responses through the real validators.  ``--live`` requires an explicit
flag and an injected client module; this repository ships no live provider
adapter and the script itself never creates a spending ledger.

The harness compares local/exact routing, Jev-assisted routing and a direct
primary classifier.  It caches by case, router/model and prompt hash, preserves
failures, and keeps automatic routing disabled unless the holdout gate passes.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.domain.commands import parse_exact_command  # noqa: E402
from app.domain.contracts import (  # noqa: E402
    ACTION_TYPES,
    AdjustSpeakingSpeed,
    Intent,
    OpenMemoryExplorer,
    ProposeContextExclusion,
    RestoreContextOperation,
    SearchMemory,
    SelectApprovedVoice,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    ShowConversationHistory,
    UserSettings,
    apply_settings_action,
    parse_action,
)
from app.domain.routing import (  # noqa: E402
    JEV_MODEL,
    RoutingOutcome,
    authorize_automatic_action,
    build_classification_state,
    build_systemone_request,
)
from app.services.action_extraction import (  # noqa: E402
    ExtractionOutcome,
    build_extraction_prompt,
    extract_action,
)
from app.services.jev import JevClient  # noqa: E402

CASES_PATH = ROOT / 'tests/fixtures/day2_routing_cases.json'
DEFAULT_CACHE_PATH = ROOT / 'artifacts/day2-routing-benchmark/cache.json'
DEFAULT_REPORT_PATH = ROOT / 'artifacts/day2-routing-benchmark/report.json'
GATE_INTENT_AGREEMENT = 0.90
GATE_MAX_MEDIAN_OVERHEAD_MS = 250.0
HOLDOUT_SIZE = 16
TUNING_SIZE = 8
ALL_INTENTS = {intent.value for intent in Intent}
ALL_TIERS = {1, 2, 3, 4, 5}
REQUIRED_TAGS = {'ambiguity', 'negation', 'quotation', 'compound'}
EXACT_ACTION_INTENT: dict[type, Intent] = {
    AdjustSpeakingSpeed: Intent.VOICE_SETTING,
    SetSpeakingSpeed: Intent.VOICE_SETTING,
    SelectApprovedVoice: Intent.VOICE_SETTING,
    SetCaptionsVisible: Intent.UI_CONTROL,
    ShowConversationHistory: Intent.CONVERSATION_NAVIGATION,
    OpenMemoryExplorer: Intent.UI_CONTROL,
}
ACTION_NAMES = {model.__name__ for model in ACTION_TYPES}


@dataclass(frozen=True)
class Case:
    id: str
    split: str
    text: str
    expected_intent: str
    expected_tier: int
    expected_complexity: str
    expected_action_kind: str | None
    expected_clarification: bool
    tags: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class Fixture:
    version: int
    frozen_at: str
    holdout_sha256: str
    approved_voices: tuple[str, ...]
    allowed_operation_ids: tuple[str, ...]
    cases: tuple[Case, ...]


@dataclass(frozen=True)
class Price:
    input_per_million: float | None
    output_per_million: float | None


@dataclass(frozen=True)
class RouterOutput:
    intent: str | None = None
    tier: int | None = None
    complexity: str | None = None
    action: Mapping[str, Any] | None = None
    authorized: bool = False
    requests: int = 0
    usage_input: int = 0
    usage_output: int = 0
    reason: str | None = None
    error: str | None = None
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    router_id: str
    model: str
    prompt_hash: str
    intent: str | None
    tier: int | None
    complexity: str | None
    action: dict[str, Any] | None
    authorized: bool
    requests: int
    usage_input: int
    usage_output: int
    elapsed_ms: float
    reason: str | None
    error: str | None
    raw: dict[str, Any] | None


class Router(Protocol):
    id: str
    model: str

    def prompt_hash(self, case: Case) -> str: ...

    async def route(self, case: Case) -> RouterOutput: ...


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def holdout_digest(cases: Sequence[Case]) -> str:
    holdout = [asdict(case) for case in cases if case.split == 'holdout']
    return hashlib.sha256(_canonical(holdout).encode()).hexdigest()


def load_fixture(path: Path = CASES_PATH) -> Fixture:
    data = json.loads(Path(path).read_text())
    cases: list[Case] = []
    for raw in data.get('cases', []):
        expected = raw['expected']
        cases.append(Case(
            id=raw['id'],
            split=raw['split'],
            text=raw['text'],
            expected_intent=expected['intent'],
            expected_tier=expected['tier'],
            expected_complexity=expected['complexity'],
            expected_action_kind=expected.get('action_kind'),
            expected_clarification=bool(expected['clarification']),
            tags=tuple(raw.get('tags', ())),
            rationale=raw.get('rationale', ''),
        ))
    fixture = Fixture(
        version=int(data['version']),
        frozen_at=str(data['frozen_at']),
        holdout_sha256=str(data['holdout_sha256']),
        approved_voices=tuple(data.get('approved_voices', ())),
        allowed_operation_ids=tuple(data.get('allowed_operation_ids', ())),
        cases=tuple(cases),
    )
    validate_fixture(fixture)
    return fixture


def validate_fixture(fixture: Fixture) -> None:
    cases = fixture.cases
    if len(cases) != TUNING_SIZE + HOLDOUT_SIZE:
        raise ValueError(f'expected 24 cases, found {len(cases)}')
    if sum(case.split == 'tuning' for case in cases) != TUNING_SIZE:
        raise ValueError('expected exactly 8 tuning cases')
    if sum(case.split == 'holdout' for case in cases) != HOLDOUT_SIZE:
        raise ValueError('expected exactly 16 holdout cases')
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate case id')
    if {case.expected_intent for case in cases} != ALL_INTENTS:
        raise ValueError('all eight intents must be covered')
    if {case.expected_tier for case in cases} != ALL_TIERS:
        raise ValueError('all five tiers must be covered')
    if not REQUIRED_TAGS <= {tag for case in cases for tag in case.tags}:
        raise ValueError('ambiguity, negation, quotation and compound tags must be covered')
    for case in cases:
        if case.split not in {'tuning', 'holdout'}:
            raise ValueError(f'{case.id}: invalid split')
        if case.expected_intent not in ALL_INTENTS:
            raise ValueError(f'{case.id}: invalid intent')
        if case.expected_tier not in ALL_TIERS:
            raise ValueError(f'{case.id}: invalid tier')
        if case.expected_complexity not in {'simple', 'complex'}:
            raise ValueError(f'{case.id}: invalid complexity')
        if case.expected_action_kind is not None and case.expected_action_kind not in ACTION_NAMES:
            raise ValueError(f'{case.id}: unknown action kind')
        if case.expected_clarification and case.expected_action_kind is not None:
            raise ValueError(f'{case.id}: clarification cases cannot label an action')
        if not case.text.strip():
            raise ValueError(f'{case.id}: empty text')
    digest = holdout_digest(cases)
    if digest != fixture.holdout_sha256:
        raise ValueError(
            f'holdout split changed: expected {fixture.holdout_sha256}, found {digest}'
        )


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _local_output(action) -> RouterOutput:
    return RouterOutput(
        intent=EXACT_ACTION_INTENT[type(action)].value,
        tier=1,
        complexity='simple',
        action=action.model_dump(mode='json'),
        authorized=True,
        requests=0,
        raw={'source': 'local_exact'},
    )


class LocalRouter:
    id = 'local_exact'
    model = 'local-exact'

    def prompt_hash(self, case: Case) -> str:
        return _prompt_hash('local-exact:' + case.text)

    async def route(self, case: Case) -> RouterOutput:
        action = parse_exact_command(case.text)
        if action is None:
            return RouterOutput(requests=0)
        return _local_output(action)


class JevAssistedRouter:
    """Exact first, then one Jev classification and at most one extraction."""

    id = 'jev_assisted'
    model = JEV_MODEL

    def __init__(
        self,
        fixture: Fixture,
        *,
        classify: Callable[[str, str, str], Awaitable[RoutingOutcome]],
        extract: Callable[[str, str], Awaitable[ExtractionOutcome]],
    ):
        self.fixture = fixture
        self._classify = classify
        self._extract = extract

    def prompt_hash(self, case: Case) -> str:
        state = build_classification_state(case.text)
        request = build_systemone_request(state)
        extraction = build_extraction_prompt(
            case.text,
            approved_voice_ids=self.fixture.approved_voices,
            allowed_operation_ids=self.fixture.allowed_operation_ids,
        )
        return _prompt_hash(_canonical({'classification': request, 'extraction': extraction}))

    async def route(self, case: Case) -> RouterOutput:
        exact = parse_exact_command(case.text)
        if exact is not None:
            return _local_output(exact)

        outcome = await self._classify(f'bench:{case.id}', f'bench:{case.id}', case.text)
        usage_input = outcome.usage.input_tokens if outcome.usage else 0
        usage_output = outcome.usage.output_tokens if outcome.usage else 0
        if outcome.kind == 'bypass':
            return RouterOutput(
                requests=1,
                usage_input=usage_input,
                usage_output=usage_output,
                reason=outcome.reason,
                error=outcome.reason or 'BYPASS',
                raw=outcome.model_dump(mode='json'),
            )

        decision = outcome.decision
        assert decision is not None
        action = None
        typed_action = None
        requests = 1
        reason = outcome.reason
        raw: dict[str, Any] = {'classification': outcome.model_dump(mode='json')}

        if outcome.automatic_action_eligible:
            extraction = await self._extract(f'bench:{case.id}', case.text)
            requests += 1
            usage_input += extraction.usage.input_tokens if extraction.usage else 0
            usage_output += extraction.usage.output_tokens if extraction.usage else 0
            reason = extraction.reason
            raw['extraction'] = extraction.model_dump(mode='json')
            if extraction.kind == 'action' and extraction.action is not None:
                typed_action = extraction.action
                action = extraction.action.model_dump(mode='json')

        return RouterOutput(
            intent=decision.intent.value,
            tier=decision.tier,
            complexity=decision.complexity.value,
            action=action,
            authorized=authorize_automatic_action(outcome, typed_action),
            requests=requests,
            usage_input=usage_input,
            usage_output=usage_output,
            reason=reason,
            raw=raw,
        )


class ScriptedJevRouter(JevAssistedRouter):
    """Replay recorded responses through the real Jev and extraction validators."""

    def __init__(self, fixture: Fixture, script: Mapping[str, Any]):
        self.script = script
        super().__init__(
            fixture,
            classify=self._scripted_classify,
            extract=self._scripted_extract,
        )

    @staticmethod
    async def _allow_reservation(_request) -> bool:
        return True

    async def _scripted_classify(self, request_id: str, turn_id: str, text: str) -> RoutingOutcome:
        entry = self.script.get(turn_id.removeprefix('bench:'))
        if not isinstance(entry, Mapping) or 'classification' not in entry:
            raise RuntimeError('MISSING_SCRIPTED_CLASSIFICATION')
        payload = entry['classification']
        status = 200
        if isinstance(payload, Mapping) and '_http_status' in payload:
            status = int(payload['_http_status'])
            payload = payload.get('body', {})
        transport = httpx.MockTransport(lambda _request: httpx.Response(status, json=payload))
        client = JevClient(
            'scripted-key',
            self._allow_reservation,
            transport=transport,
        )
        return await client.classify(
            request_id=request_id,
            turn_id=turn_id,
            instruction=text,
        )

    async def _scripted_extract(self, request_id: str, text: str) -> ExtractionOutcome:
        case_id = request_id.removeprefix('bench:')
        entry = self.script.get(case_id)
        if not isinstance(entry, Mapping) or 'extraction' not in entry:
            return ExtractionOutcome(
                kind='clarification',
                source='none',
                reason='MISSING_SCRIPTED_EXTRACTION',
            )
        raw = entry['extraction']

        async def generate(_prompt: str):
            return raw

        return await extract_action(
            text,
            request_id=request_id,
            reserve=self._allow_reservation,
            generate=generate,
            approved_voice_ids=self.fixture.approved_voices,
            allowed_operation_ids=self.fixture.allowed_operation_ids,
        )


class ScriptedDirectRouter:
    id = 'direct'
    model = 'deepseek-flash'

    def __init__(self, script: Mapping[str, Any]):
        self.script = script

    def prompt_hash(self, case: Case) -> str:
        return _prompt_hash(_canonical({'router': 'direct', 'text': case.text}))

    async def route(self, case: Case) -> RouterOutput:
        entry = self.script.get(case.id)
        if not isinstance(entry, Mapping) or 'direct' not in entry:
            return RouterOutput(requests=1, error='MISSING_SCRIPTED_DIRECT')
        direct = entry['direct']
        if not isinstance(direct, Mapping):
            return RouterOutput(requests=1, error='INVALID_SCRIPTED_DIRECT')
        if direct.get('error'):
            return RouterOutput(requests=1, error=str(direct['error']))

        action_payload = direct.get('action')
        action = None
        if action_payload is not None:
            if not isinstance(action_payload, Mapping):
                return RouterOutput(requests=1, error='INVALID_SCRIPTED_ACTION')
            if action_payload.get('kind') == 'NoAction':
                action_payload = None
            else:
                try:
                    action = parse_action(dict(action_payload))
                except (ValueError, TypeError, OverflowError):
                    return RouterOutput(requests=1, error='INVALID_SCRIPTED_ACTION')

        usage = direct.get('usage', {})
        return RouterOutput(
            intent=direct.get('intent'),
            tier=direct.get('tier'),
            complexity=direct.get('complexity'),
            action=action.model_dump(mode='json') if action is not None else None,
            authorized=action is not None,
            requests=1,
            usage_input=int(usage.get('input_tokens', 0)) if isinstance(usage, Mapping) else 0,
            usage_output=int(usage.get('output_tokens', 0)) if isinstance(usage, Mapping) else 0,
            raw=dict(direct),
        )


class ResultCache:
    """One JSON object keyed by case, router/model and prompt hash."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get('schema_version') != 1:
                raise ValueError('unsupported benchmark cache schema')
            entries = data.get('entries')
            if not isinstance(entries, Mapping):
                raise ValueError('benchmark cache entries must be an object')
            self.entries = {str(key): dict(value) for key, value in entries.items()}

    @staticmethod
    def key(case_id: str, router: Router, prompt_hash: str) -> str:
        return hashlib.sha256(
            _canonical({
                'case_id': case_id,
                'router_id': router.id,
                'model': router.model,
                'prompt_hash': prompt_hash,
            }).encode()
        ).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        self.entries[key] = dict(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + '.tmp')
        temporary.write_text(json.dumps(
            {'schema_version': 1, 'entries': self.entries},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + '\n')
        temporary.replace(self.path)


@dataclass
class IsolatedActionState:
    """Per-case state. Settings change locally; external effects are mocked."""

    settings: UserSettings = field(default_factory=UserSettings)
    navigation: str | None = None
    memory_queries: list[str] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    restored_operations: list[str] = field(default_factory=list)
    external_effects: list[str] = field(default_factory=list)

    def apply(self, action) -> dict[str, Any]:
        if isinstance(action, (AdjustSpeakingSpeed, SetSpeakingSpeed, SelectApprovedVoice, SetCaptionsVisible)):
            before = self.settings
            self.settings = apply_settings_action(before, action)
            return {'changed': self.settings != before, 'settings': self.settings.model_dump(mode='json')}
        if isinstance(action, ShowConversationHistory):
            self.navigation = 'history'
            return {'navigation': 'history'}
        if isinstance(action, OpenMemoryExplorer):
            self.navigation = 'memory_explorer'
            return {'navigation': 'memory_explorer'}
        if isinstance(action, SearchMemory):
            self.memory_queries.append(action.query)
            self.external_effects.append('memory-read')
            return {'mocked': 'memory-read', 'query': action.query}
        if isinstance(action, ProposeContextExclusion):
            self.proposals.append(action.model_dump(mode='json'))
            self.external_effects.append('memory-preview')
            return {'proposal': action.model_dump(mode='json')}
        if isinstance(action, RestoreContextOperation):
            self.restored_operations.append(action.operation_id)
            self.external_effects.append('context-restore')
            return {'mocked': 'context-restore', 'operation_id': action.operation_id}
        return {'changed': False}


def evaluate_result(case: Case, result: CaseResult) -> dict[str, Any]:
    action = result.action
    action_kind = action.get('kind') if isinstance(action, Mapping) else None
    invalid_action = False
    effect: dict[str, Any] | None = None
    if action is not None:
        try:
            typed = parse_action(dict(action))
            effect = IsolatedActionState().apply(typed)
        except (ValueError, TypeError, OverflowError):
            invalid_action = True
            effect = {'invalid': True}

    expected_kind = case.expected_action_kind
    unsafe_proposal = action_kind is not None and (expected_kind is None or action_kind != expected_kind)
    if invalid_action:
        unsafe_proposal = True
    unsafe_authorization = bool(result.authorized and unsafe_proposal)
    return {
        'case_id': case.id,
        'split': case.split,
        'intent_agree': result.intent == case.expected_intent,
        'tier_agree': result.tier == case.expected_tier,
        'complexity_agree': result.complexity == case.expected_complexity,
        'action_kind': action_kind,
        'expected_action_kind': expected_kind,
        'unsafe_proposal': unsafe_proposal,
        'unsafe_authorization': unsafe_authorization,
        'invalid_action': invalid_action,
        'effect': effect,
    }


def _median(values: Sequence[float]) -> float | None:
    return round(float(statistics.median(values)), 3) if values else None


def _cost(price: Price | None, input_tokens: int, output_tokens: int) -> float | None:
    if price is None:
        return None
    if price.input_per_million is None or price.output_per_million is None:
        return None
    return round(
        (input_tokens / 1_000_000) * price.input_per_million
        + (output_tokens / 1_000_000) * price.output_per_million,
        9,
    )


def aggregate_results(
    cases: Sequence[Case],
    results: Sequence[CaseResult],
    *,
    prices: Mapping[str, Price] | None = None,
) -> dict[str, Any]:
    by_id = {case.id: case for case in cases}
    prices = prices or {}
    router_ids = sorted({result.router_id for result in results})
    aggregates: dict[str, Any] = {}
    for router_id in router_ids:
        entries = [result for result in results if result.router_id == router_id]
        evaluations = [evaluate_result(by_id[result.case_id], result) for result in entries]
        latencies = [result.elapsed_ms for result in entries]
        input_tokens = sum(result.usage_input for result in entries)
        output_tokens = sum(result.usage_output for result in entries)
        model = entries[0].model if entries else ''
        aggregates[router_id] = {
            'model': model,
            'sample_size': len(entries),
            'intent_agreement': round(sum(item['intent_agree'] for item in evaluations) / len(entries), 6) if entries else None,
            'tier_agreement': round(sum(item['tier_agree'] for item in evaluations) / len(entries), 6) if entries else None,
            'complexity_agreement': round(sum(item['complexity_agree'] for item in evaluations) / len(entries), 6) if entries else None,
            'action_proposals': sum(result.action is not None for result in entries),
            'unsafe_action_proposals': sum(item['unsafe_proposal'] for item in evaluations),
            'unsafe_authorizations': sum(item['unsafe_authorization'] for item in evaluations),
            'invalid_action_proposals': sum(item['invalid_action'] for item in evaluations),
            'requests_issued': sum(result.requests for result in entries),
            'requests_avoided_vs_one_direct_call': len(entries) - sum(result.requests for result in entries),
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'estimated_cost_usd': _cost(prices.get(model), input_tokens, output_tokens),
            'raw_latency_ms': [round(result.elapsed_ms, 3) for result in entries],
            'median_latency_ms': _median(latencies),
            'failures': [
                {'case_id': result.case_id, 'error': result.error}
                for result in entries if result.error is not None
            ],
            'failure_count': sum(result.error is not None for result in entries),
        }
    return aggregates


def holdout_gate(
    cases: Sequence[Case],
    results: Sequence[CaseResult],
    *,
    intent_threshold: float = GATE_INTENT_AGREEMENT,
    max_median_overhead_ms: float = GATE_MAX_MEDIAN_OVERHEAD_MS,
) -> dict[str, Any]:
    holdout = [case for case in cases if case.split == 'holdout']
    by_id = {case.id: case for case in holdout}
    jev = {
        result.case_id: result
        for result in results
        if result.router_id == 'jev_assisted' and result.case_id in by_id
    }
    direct = {
        result.case_id: result
        for result in results
        if result.router_id == 'direct' and result.case_id in by_id
    }

    measured = len(jev)
    matches = sum(
        jev[case.id].intent == case.expected_intent
        for case in holdout if case.id in jev
    )
    agreement = matches / len(holdout) if holdout else 0.0
    unsafe = sum(
        evaluate_result(case, jev[case.id])['unsafe_authorization']
        for case in holdout if case.id in jev
    )
    overheads = [
        jev[case.id].elapsed_ms - direct[case.id].elapsed_ms
        for case in holdout
        if case.id in jev and case.id in direct
        and jev[case.id].error is None and direct[case.id].error is None
    ]
    median_overhead = _median(overheads)
    reasons: list[str] = []
    if measured != HOLDOUT_SIZE:
        reasons.append(f'INCOMPLETE_HOLDOUT:{measured}/{HOLDOUT_SIZE}')
    if agreement < intent_threshold:
        reasons.append('INTENT_AGREEMENT_BELOW_90_PERCENT')
    if unsafe != 0:
        reasons.append('UNSAFE_AUTHORIZATION')
    if median_overhead is None:
        reasons.append('NO_PAIRED_LATENCY')
    elif median_overhead > max_median_overhead_ms:
        reasons.append('MEDIAN_OVERHEAD_ABOVE_250MS')
    return {
        'passed': not reasons,
        'holdout_sample_size': len(holdout),
        'held_out_measured': measured,
        'intent_agreement': round(agreement, 6),
        'intent_threshold': intent_threshold,
        'unsafe_authorizations': unsafe,
        'median_added_overhead_ms': median_overhead,
        'max_median_overhead_ms': max_median_overhead_ms,
        'paired_latency_samples': len(overheads),
        'reasons': reasons,
    }


def _result_from_output(
    case: Case,
    router: Router,
    output: RouterOutput,
    elapsed_ms: float,
    prompt_hash: str,
) -> CaseResult:
    action = dict(output.action) if isinstance(output.action, Mapping) else None
    return CaseResult(
        case_id=case.id,
        router_id=router.id,
        model=router.model,
        prompt_hash=prompt_hash,
        intent=output.intent,
        tier=output.tier,
        complexity=output.complexity,
        action=action,
        authorized=bool(output.authorized),
        requests=int(output.requests),
        usage_input=int(output.usage_input),
        usage_output=int(output.usage_output),
        elapsed_ms=round(float(elapsed_ms), 3),
        reason=output.reason,
        error=output.error,
        raw=dict(output.raw) if isinstance(output.raw, Mapping) else None,
    )


async def run_benchmark(
    fixture: Fixture,
    routers: Sequence[Router],
    cache: ResultCache,
    *,
    mode: str = 'scripted',
    prices: Mapping[str, Price] | None = None,
) -> tuple[list[CaseResult], dict[str, Any]]:
    results: list[CaseResult] = []
    for router in routers:
        for case in fixture.cases:
            prompt_hash = router.prompt_hash(case)
            key = ResultCache.key(case.id, router, prompt_hash)
            cached = cache.get(key)
            if cached is not None:
                results.append(CaseResult(**cached))
                continue

            started = time.perf_counter()
            try:
                output = await router.route(case)
            except Exception as exc:  # preserve the failure; never drop the case
                output = RouterOutput(requests=0, error=f'EXCEPTION:{type(exc).__name__}')
            elapsed_ms = (time.perf_counter() - started) * 1000
            result = _result_from_output(case, router, output, elapsed_ms, prompt_hash)
            cache.put(key, asdict(result))
            results.append(result)
    return results, build_report(fixture, results, mode=mode, prices=prices)


def build_report(
    fixture: Fixture,
    results: Sequence[CaseResult],
    *,
    mode: str = 'scripted',
    prices: Mapping[str, Price] | None = None,
) -> dict[str, Any]:
    cases = fixture.cases
    case_report = []
    by_id = {case.id: case for case in cases}
    for result in results:
        evaluated = evaluate_result(by_id[result.case_id], result)
        case_report.append({**asdict(result), **evaluated})
    prices = prices or {JEV_MODEL: Price(0.042, 0.0), 'local-exact': Price(0.0, 0.0)}
    return {
        'schema_version': 1,
        'mode': mode,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'fixture': {
            'version': fixture.version,
            'frozen_at': fixture.frozen_at,
            'holdout_sha256': fixture.holdout_sha256,
            'total_cases': len(cases),
            'tuning_cases': sum(case.split == 'tuning' for case in cases),
            'holdout_cases': sum(case.split == 'holdout' for case in cases),
            'intents': sorted({case.expected_intent for case in cases}),
            'tiers': sorted({case.expected_tier for case in cases}),
            'tags': sorted({tag for case in cases for tag in case.tags}),
        },
        'pricing': {model: asdict(value) for model, value in prices.items()},
        'routers': aggregate_results(cases, results, prices=prices),
        'holdout_gate': holdout_gate(cases, results),
        'automatic_routing_enabled': False,
        'limitations': [
            'Automatic routing may only be enabled after a reviewed live run passes the holdout gate.',
            'A scripted run validates the harness and validators; it is not live model evidence.',
            'Direct-model pricing is recorded only when explicitly supplied by the operator.',
        ],
        'results': case_report,
    }


def dry_run_report(fixture: Fixture) -> dict[str, Any]:
    return {
        'schema_version': 1,
        'mode': 'dry_run',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'fixture': {
            'version': fixture.version,
            'frozen_at': fixture.frozen_at,
            'holdout_sha256': fixture.holdout_sha256,
            'total_cases': len(fixture.cases),
            'tuning_cases': sum(case.split == 'tuning' for case in fixture.cases),
            'holdout_cases': sum(case.split == 'holdout' for case in fixture.cases),
            'intents': sorted({case.expected_intent for case in fixture.cases}),
            'tiers': sorted({case.expected_tier for case in fixture.cases}),
            'tags': sorted({tag for case in fixture.cases for tag in case.tags}),
        },
        'routers': {},
        'holdout_gate': {
            'passed': False,
            'reasons': ['NOT_EVALUATED'],
            'holdout_sample_size': sum(case.split == 'holdout' for case in fixture.cases),
        },
        'automatic_routing_enabled': False,
        'limitations': ['Dry run: no provider calls were made and no gate result exists.'],
    }


def load_script(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    responses = data.get('responses', data)
    if not isinstance(responses, Mapping):
        raise ValueError('script responses must be an object')
    return dict(responses)


def scripted_routers(fixture: Fixture, script: Mapping[str, Any]) -> list[Router]:
    return [LocalRouter(), ScriptedJevRouter(fixture, script), ScriptedDirectRouter(script)]


def _build_live_routers(module_path: Path, fixture: Fixture, cache: ResultCache) -> Sequence[Router]:
    """Import an operator-supplied module; this script owns no live adapter."""
    spec = importlib.util.spec_from_file_location('sarjy_live_routers', module_path)
    if spec is None or spec.loader is None:
        raise ValueError('could not load live router module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder = getattr(module, 'build_routers', None)
    if not callable(builder):
        raise ValueError('live router module must define build_routers(fixture, cache)')
    routers = builder(fixture=fixture, cache=cache)
    if not isinstance(routers, Sequence):
        raise ValueError('build_routers must return a sequence of routers')
    return list(routers)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='validate fixtures only (default)')
    mode.add_argument('--scripted', type=Path, help='replay recorded responses from this JSON file')
    mode.add_argument('--live', action='store_true', help='explicitly allow provider calls')
    parser.add_argument('--cases', type=Path, default=CASES_PATH)
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--live-module', type=Path, help='module exposing build_routers(fixture, cache)')
    parser.add_argument('--direct-input-price', type=float)
    parser.add_argument('--direct-output-price', type=float)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    fixture = load_fixture(args.cases)

    if args.live and args.live_module is None:
        raise SystemExit('--live requires --live-module; no unreviewed live adapter is built in')
    if args.scripted is None and not args.live:
        report = dry_run_report(fixture)
        text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        print(text)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text + '\n')
        return 0

    cache = ResultCache(args.cache)
    if args.live:
        routers = _build_live_routers(args.live_module, fixture, cache)
        mode = 'live'
    else:
        routers = scripted_routers(fixture, load_script(args.scripted))
        mode = 'scripted'

    prices: dict[str, Price] = {
        JEV_MODEL: Price(0.042, 0.0),
        'local-exact': Price(0.0, 0.0),
    }
    if args.direct_input_price is not None or args.direct_output_price is not None:
        if args.direct_input_price is None or args.direct_output_price is None:
            raise SystemExit('both direct price options are required together')
        prices['deepseek-flash'] = Price(args.direct_input_price, args.direct_output_price)

    results, report = asyncio.run(run_benchmark(fixture, routers, cache, mode=mode, prices=prices))
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
