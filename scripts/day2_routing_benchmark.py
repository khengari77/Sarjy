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
from dataclasses import asdict, dataclass, field, fields
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
    EXTRACTION_MODEL,
    ExtractionOutcome,
    GenerationConfig,
    GenerationRequest,
    build_extraction_prompt,
    extract_action,
)
from app.services.jev import JevClient  # noqa: E402

CASES_PATH = ROOT / 'tests/fixtures/day2_routing_cases.json'
DEFAULT_SCRIPTED_CACHE_PATH = ROOT / 'artifacts/day2-routing-benchmark/cache-scripted.json'
DEFAULT_LIVE_CACHE_PATH = ROOT / 'artifacts/day2-routing-benchmark/cache-live.json'
DEFAULT_REPORT_PATH = ROOT / 'artifacts/day2-routing-benchmark/report.json'
CACHE_SCHEMA_VERSION = 2
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
    expected_effect: dict[str, Any] | None
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
class UsageRecord:
    provider: str
    model: str
    purpose: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RouterOutput:
    intent: str | None = None
    tier: int | None = None
    complexity: str | None = None
    action: Mapping[str, Any] | None = None
    authorized: bool = False
    requests: int = 0
    usage: tuple[UsageRecord, ...] = ()
    reason: str | None = None
    error: str | None = None
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    router_id: str
    model: str
    prompt_hash: str
    mode: str
    intent: str | None
    tier: int | None
    complexity: str | None
    action: dict[str, Any] | None
    authorized: bool
    requests: int
    usage: list[dict[str, Any]]
    elapsed_ms: float
    reason: str | None
    error: str | None
    raw: dict[str, Any] | None


class Router(Protocol):
    id: str
    model: str
    provenance: str

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
            expected_effect=expected.get('effect'),
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
        if (case.expected_action_kind is None) != (case.expected_effect is None):
            raise ValueError(f'{case.id}: action kind and expected effect must appear together')
        if case.expected_effect is not None:
            if not isinstance(case.expected_effect, dict) or not case.expected_effect:
                raise ValueError(f'{case.id}: expected effect must be a non-empty object')
            unknown = set(case.expected_effect) - {
                'settings', 'navigation', 'memory_query_terms',
                'proposal_terms', 'restored_operation_id',
            }
            if unknown:
                raise ValueError(f'{case.id}: unsupported expected effect keys {sorted(unknown)}')
            if case.expected_action_kind in {
                'AdjustSpeakingSpeed', 'SetSpeakingSpeed', 'SelectApprovedVoice', 'SetCaptionsVisible'
            } and 'settings' not in case.expected_effect:
                raise ValueError(f'{case.id}: settings action requires expected settings')
            if case.expected_action_kind in {'ShowConversationHistory', 'OpenMemoryExplorer'} and 'navigation' not in case.expected_effect:
                raise ValueError(f'{case.id}: navigation action requires expected navigation')
            if case.expected_action_kind == 'SearchMemory' and 'memory_query_terms' not in case.expected_effect:
                raise ValueError(f'{case.id}: search action requires expected query terms')
            if case.expected_action_kind == 'ProposeContextExclusion' and 'proposal_terms' not in case.expected_effect:
                raise ValueError(f'{case.id}: proposal action requires expected proposal terms')
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
    provenance = 'local'

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
    provenance = 'live'

    def __init__(
        self,
        fixture: Fixture,
        *,
        classify: Callable[[str, str, str], Awaitable[RoutingOutcome]],
        extract: Callable[[str, str], Awaitable[ExtractionOutcome]],
        extraction_model: str = EXTRACTION_MODEL,
    ):
        self.fixture = fixture
        self._classify = classify
        self._extract = extract
        self.extraction_model = extraction_model

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
        classification_usage = ()
        if outcome.usage is not None:
            classification_usage = (
                UsageRecord(
                    provider='jev',
                    model=JEV_MODEL,
                    purpose='classification',
                    input_tokens=outcome.usage.input_tokens,
                    output_tokens=outcome.usage.output_tokens,
                ),
            )
        if outcome.kind == 'bypass':
            return RouterOutput(
                requests=1,
                usage=classification_usage,
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
        usage = classification_usage
        raw: dict[str, Any] = {'classification': outcome.model_dump(mode='json')}

        if outcome.automatic_action_eligible:
            extraction = await self._extract(f'bench:{case.id}', case.text)
            requests += 1
            if extraction.usage is not None:
                usage = (*usage, UsageRecord(
                    provider='deepseek',
                    model=self.extraction_model,
                    purpose='extraction',
                    input_tokens=extraction.usage.input_tokens,
                    output_tokens=extraction.usage.output_tokens,
                ))
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
            usage=usage,
            reason=reason,
            raw=raw,
        )


class ScriptedJevRouter(JevAssistedRouter):
    """Replay recorded responses through the real Jev and extraction validators."""

    provenance = 'scripted'

    def __init__(
        self,
        fixture: Fixture,
        script: Mapping[str, Any],
        generation: GenerationConfig,
    ):
        self.script = script
        self.generation = generation
        super().__init__(
            fixture,
            classify=self._scripted_classify,
            extract=self._scripted_extract,
            extraction_model=generation.model,
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

        async def generate(_request: GenerationRequest):
            return raw

        return await extract_action(
            text,
            request_id=request_id,
            reserve=self._allow_reservation,
            generate=generate,
            generation=self.generation,
            approved_voice_ids=self.fixture.approved_voices,
            allowed_operation_ids=self.fixture.allowed_operation_ids,
        )


class ScriptedDirectRouter:
    id = 'direct'
    model = 'deepseek-flash'
    provenance = 'scripted'

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
        input_tokens = int(usage.get('input_tokens', 0)) if isinstance(usage, Mapping) else 0
        output_tokens = int(usage.get('output_tokens', 0)) if isinstance(usage, Mapping) else 0
        return RouterOutput(
            intent=direct.get('intent'),
            tier=direct.get('tier'),
            complexity=direct.get('complexity'),
            action=action.model_dump(mode='json') if action is not None else None,
            authorized=action is not None,
            requests=1,
            usage=(UsageRecord(
                provider='deepseek',
                model='deepseek-flash',
                purpose='direct',
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),),
            raw=dict(direct),
        )


class ResultCache:
    """One mode-specific JSON cache keyed by case, model and prompt hash."""

    def __init__(self, path: Path, mode: str):
        if mode not in {'scripted', 'live'}:
            raise ValueError('cache mode must be scripted or live')
        self.path = Path(path)
        self.mode = mode
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get('schema_version') != CACHE_SCHEMA_VERSION:
                raise ValueError('unsupported benchmark cache schema')
            if data.get('mode') != self.mode:
                raise ValueError(
                    f'cache provenance mismatch: cache mode {data.get("mode")!r}, '
                    f'requested {self.mode!r}'
                )
            entries = data.get('entries')
            if not isinstance(entries, Mapping):
                raise ValueError('benchmark cache entries must be an object')
            self.entries = {str(key): dict(value) for key, value in entries.items()}

    @staticmethod
    def key(case_id: str, router: Router, prompt_hash: str, mode: str) -> str:
        return hashlib.sha256(
            _canonical({
                'mode': mode,
                'case_id': case_id,
                'router_id': router.id,
                'model': router.model,
                'provenance': router.provenance,
                'prompt_hash': prompt_hash,
            }).encode()
        ).hexdigest()

    def get(self, key: str, *, case_id: str, router: Router, prompt_hash: str) -> dict[str, Any] | None:
        entry = self.entries.get(key)
        if entry is None:
            return None
        expected = {
            'cache_key': key,
            'mode': self.mode,
            'case_id': case_id,
            'router_id': router.id,
            'model': router.model,
            'provenance': router.provenance,
            'prompt_hash': prompt_hash,
        }
        for field_name, value in expected.items():
            if entry.get(field_name) != value:
                raise ValueError(
                    f'cache provenance mismatch for {case_id}: '
                    f'{field_name}={entry.get(field_name)!r}, expected {value!r}'
                )
        return entry

    def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        case_id: str,
        router: Router,
        prompt_hash: str,
    ) -> None:
        entry = dict(value)
        entry.update({
            'cache_key': key,
            'mode': self.mode,
            'case_id': case_id,
            'router_id': router.id,
            'model': router.model,
            'prompt_hash': prompt_hash,
            'provenance': router.provenance,
        })
        self.entries[key] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + '.tmp')
        temporary.write_text(json.dumps(
            {
                'schema_version': CACHE_SCHEMA_VERSION,
                'mode': self.mode,
                'entries': self.entries,
            },
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


def _effect_matches(expected: Mapping[str, Any] | None, actual: Mapping[str, Any] | None) -> bool:
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        return False
    if 'settings' in expected:
        expected_settings = expected['settings']
        actual_settings = actual.get('settings')
        if not isinstance(expected_settings, Mapping) or not isinstance(actual_settings, Mapping):
            return False
        if any(actual_settings.get(key) != value for key, value in expected_settings.items()):
            return False
    if 'navigation' in expected and actual.get('navigation') != expected['navigation']:
        return False
    if 'memory_query_terms' in expected:
        query = str(actual.get('query', '')).casefold()
        terms = expected['memory_query_terms']
        if not isinstance(terms, Sequence) or isinstance(terms, str):
            return False
        if not all(str(term).casefold() in query for term in terms):
            return False
    if 'proposal_terms' in expected:
        proposal = actual.get('proposal')
        if not isinstance(proposal, Mapping):
            return False
        proposal_text = ' '.join(
            [str(proposal.get('query', ''))]
            + [str(value) for value in proposal.get('keep', ())]
        ).casefold()
        terms = expected['proposal_terms']
        if not isinstance(terms, Sequence) or isinstance(terms, str):
            return False
        if not all(str(term).casefold() in proposal_text for term in terms):
            return False
    if 'restored_operation_id' in expected:
        if actual.get('operation_id') != expected['restored_operation_id']:
            return False
    return True


def evaluate_result(case: Case, result: CaseResult) -> dict[str, Any]:
    action = result.action
    action_kind = action.get('kind') if isinstance(action, Mapping) else None
    invalid_action = False
    effect: dict[str, Any] | None = None
    effect_match: bool | None = None
    if action is not None:
        try:
            typed = parse_action(dict(action))
            effect = IsolatedActionState().apply(typed)
            effect_match = case.expected_action_kind == action_kind and _effect_matches(
                case.expected_effect, effect
            )
        except (ValueError, TypeError, OverflowError):
            invalid_action = True
            effect = {'invalid': True}
            effect_match = False

    expected_kind = case.expected_action_kind
    unsafe_proposal = action_kind is not None and (
        expected_kind is None or action_kind != expected_kind or not effect_match
    )
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
        'expected_effect': case.expected_effect,
        'effect_match': effect_match,
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
        usage_by_model: dict[str, dict[str, int]] = {}
        for result in entries:
            for record in result.usage:
                model = str(record.get('model', ''))
                bucket = usage_by_model.setdefault(model, {'input_tokens': 0, 'output_tokens': 0})
                bucket['input_tokens'] += int(record.get('input_tokens', 0))
                bucket['output_tokens'] += int(record.get('output_tokens', 0))
        cost_by_model: dict[str, float | None] = {
            model: _cost(prices.get(model), tokens['input_tokens'], tokens['output_tokens'])
            for model, tokens in usage_by_model.items()
        }
        unpriced_models = sorted(model for model, cost in cost_by_model.items() if cost is None)
        estimated_cost = None
        if not unpriced_models:
            estimated_cost = round(sum(cost_by_model.values()), 9)
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
            'usage_by_model': usage_by_model,
            'cost_by_model': cost_by_model,
            'unpriced_models': unpriced_models,
            'input_tokens': sum(tokens['input_tokens'] for tokens in usage_by_model.values()),
            'output_tokens': sum(tokens['output_tokens'] for tokens in usage_by_model.values()),
            'estimated_cost_usd': estimated_cost,
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
    mode: str,
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
    direct_measured = len(direct)
    matches = sum(
        jev[case.id].intent == case.expected_intent
        for case in holdout if case.id in jev
    )
    agreement = matches / len(holdout) if holdout else 0.0
    unsafe = sum(
        evaluate_result(case, jev[case.id])['unsafe_authorization']
        for case in holdout if case.id in jev
    )
    valid_pairs = [
        case.id for case in holdout
        if case.id in jev and case.id in direct
        and jev[case.id].error is None and direct[case.id].error is None
    ]
    overheads = [
        jev[case.id].elapsed_ms - direct[case.id].elapsed_ms
        for case in holdout if case.id in valid_pairs
    ]
    median_overhead = _median(overheads)
    reasons: list[str] = []
    if mode != 'live':
        reasons.append('NOT_LIVE')
    if any(result.mode != 'live' for result in jev.values()):
        reasons.append('NON_LIVE_JEV_PROVENANCE')
    if any(result.mode != 'live' for result in direct.values()):
        reasons.append('NON_LIVE_DIRECT_PROVENANCE')
    if measured != HOLDOUT_SIZE:
        reasons.append(f'INCOMPLETE_HOLDOUT:{measured}/{HOLDOUT_SIZE}')
    if direct_measured != HOLDOUT_SIZE:
        reasons.append(f'INCOMPLETE_DIRECT_RESULTS:{direct_measured}/{HOLDOUT_SIZE}')
    if agreement < intent_threshold:
        reasons.append('INTENT_AGREEMENT_BELOW_90_PERCENT')
    if unsafe != 0:
        reasons.append('UNSAFE_AUTHORIZATION')
    if len(valid_pairs) != HOLDOUT_SIZE:
        reasons.append(f'INCOMPLETE_PAIRED_LATENCY:{len(valid_pairs)}/{HOLDOUT_SIZE}')
    if median_overhead is None:
        reasons.append('NO_PAIRED_LATENCY')
    elif median_overhead > max_median_overhead_ms:
        reasons.append('MEDIAN_OVERHEAD_ABOVE_250MS')
    return {
        'passed': not reasons,
        'mode': mode,
        'holdout_sample_size': len(holdout),
        'held_out_measured': measured,
        'direct_measured': direct_measured,
        'intent_agreement': round(agreement, 6),
        'intent_threshold': intent_threshold,
        'unsafe_authorizations': unsafe,
        'median_added_overhead_ms': median_overhead,
        'max_median_overhead_ms': max_median_overhead_ms,
        'paired_latency_samples': len(overheads),
        'valid_pairs': len(valid_pairs),
        'reasons': reasons,
    }


CASE_RESULT_FIELDS = {item.name for item in fields(CaseResult)}


def _case_result_from_cache(entry: Mapping[str, Any]) -> CaseResult:
    values = {key: entry[key] for key in CASE_RESULT_FIELDS if key in entry}
    return CaseResult(**values)


def _result_from_output(
    case: Case,
    router: Router,
    output: RouterOutput,
    elapsed_ms: float,
    prompt_hash: str,
    mode: str,
) -> CaseResult:
    action = dict(output.action) if isinstance(output.action, Mapping) else None
    return CaseResult(
        case_id=case.id,
        router_id=router.id,
        model=router.model,
        prompt_hash=prompt_hash,
        mode=mode,
        intent=output.intent,
        tier=output.tier,
        complexity=output.complexity,
        action=action,
        authorized=bool(output.authorized),
        requests=int(output.requests),
        usage=[asdict(record) for record in output.usage],
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
    if mode == 'live' and any(
        router.id != 'local_exact' and router.provenance != 'live'
        for router in routers
    ):
        raise ValueError('live mode requires live-provenance provider routers')
    results: list[CaseResult] = []
    for router in routers:
        for case in fixture.cases:
            prompt_hash = router.prompt_hash(case)
            key = ResultCache.key(case.id, router, prompt_hash, mode)
            cached = cache.get(
                key, case_id=case.id, router=router, prompt_hash=prompt_hash
            )
            if cached is not None:
                results.append(_case_result_from_cache(cached))
                continue

            started = time.perf_counter()
            try:
                output = await router.route(case)
            except Exception as exc:  # preserve the failure; never drop the case
                output = RouterOutput(requests=0, error=f'EXCEPTION:{type(exc).__name__}')
            elapsed_ms = (time.perf_counter() - started) * 1000
            result = _result_from_output(case, router, output, elapsed_ms, prompt_hash, mode)
            cache.put(
                key,
                asdict(result),
                case_id=case.id,
                router=router,
                prompt_hash=prompt_hash,
            )
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
        'holdout_gate': holdout_gate(cases, results, mode=mode),
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


def extraction_generation_config(
    *,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
    max_output_tokens: int = 128,
) -> GenerationConfig:
    return GenerationConfig(
        model=EXTRACTION_MODEL,
        max_output_tokens=max_output_tokens,
        input_usd_per_million=input_usd_per_million or 0.0,
        output_usd_per_million=output_usd_per_million or 0.0,
    )


def scripted_routers(
    fixture: Fixture,
    script: Mapping[str, Any],
    generation: GenerationConfig,
) -> list[Router]:
    return [
        LocalRouter(),
        ScriptedJevRouter(fixture, script, generation),
        ScriptedDirectRouter(script),
    ]


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
    parser.add_argument('--cache', type=Path)
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

    mode = 'live' if args.live else 'scripted'
    cache_path = args.cache or (
        DEFAULT_LIVE_CACHE_PATH if mode == 'live' else DEFAULT_SCRIPTED_CACHE_PATH
    )
    cache = ResultCache(cache_path, mode)

    prices: dict[str, Price] = {
        JEV_MODEL: Price(0.042, 0.0),
        'local-exact': Price(0.0, 0.0),
    }
    if args.direct_input_price is not None or args.direct_output_price is not None:
        if args.direct_input_price is None or args.direct_output_price is None:
            raise SystemExit('both direct price options are required together')
        prices['deepseek-flash'] = Price(args.direct_input_price, args.direct_output_price)

    if args.live:
        routers = _build_live_routers(args.live_module, fixture, cache)
    else:
        generation = extraction_generation_config(
            input_usd_per_million=args.direct_input_price,
            output_usd_per_million=args.direct_output_price,
        )
        routers = scripted_routers(fixture, load_script(args.scripted), generation)

    results, report = asyncio.run(run_benchmark(fixture, routers, cache, mode=mode, prices=prices))
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
