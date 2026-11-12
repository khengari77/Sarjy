import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import day2_routing_benchmark as bench  # noqa: E402

from app.domain.contracts import (  # noqa: E402
    Intent,
    SearchMemory,
    SetSpeakingSpeed,
    ShowConversationHistory,
)


def intent_options():
    return [intent.value for intent in Intent]


def choice(names, selected, top=0.96):
    remainder = (1 - top) / (len(names) - 1)
    return {
        'type': 'choice',
        'choice': selected,
        'confidence': 0.99,
        'probabilities': {name: (top if name == selected else remainder) for name in names},
    }


TIER_OPTIONS = [
    'tier_1_short_acknowledgment',
    'tier_2_normal_conversation',
    'tier_3_moderate_explanation',
    'tier_4_detailed_explanation',
    'tier_5_explicitly_detailed',
]


def classification(case):
    return {
        'model': bench.JEV_MODEL,
        'answers': {
            'intent': choice(intent_options(), case.expected_intent),
            'response_tier': choice(TIER_OPTIONS, TIER_OPTIONS[case.expected_tier - 1]),
            'complexity': choice(['simple', 'complex'], case.expected_complexity),
        },
        'usage': {'input_tokens': 50, 'output_tokens': 5},
    }


ACTION_PAYLOAD = {
    'AdjustSpeakingSpeed': {'kind': 'AdjustSpeakingSpeed', 'delta': -0.1},
    'SetSpeakingSpeed': {'kind': 'SetSpeakingSpeed', 'speed': 1.2},
    'SetCaptionsVisible': {'kind': 'SetCaptionsVisible', 'visible': False},
    'ShowConversationHistory': {'kind': 'ShowConversationHistory'},
    'OpenMemoryExplorer': {'kind': 'OpenMemoryExplorer'},
    'SearchMemory': {'kind': 'SearchMemory', 'query': 'trip'},
    'ProposeContextExclusion': {'kind': 'ProposeContextExclusion', 'query': 'trip', 'keep': []},
    'SelectApprovedVoice': {'kind': 'SelectApprovedVoice', 'voice_id': 'aurora'},
    'RestoreContextOperation': {'kind': 'RestoreContextOperation', 'operation_id': 'operation-last-exclusion'},
}


def script_for(fixture):
    responses = {}
    for case in fixture.cases:
        action = ACTION_PAYLOAD.get(case.expected_action_kind) if case.expected_action_kind else None
        responses[case.id] = {
            'classification': classification(case),
            'extraction': action if action is not None else {'kind': 'NoAction'},
            'direct': {
                'intent': case.expected_intent,
                'tier': case.expected_tier,
                'complexity': case.expected_complexity,
                'action': action,
                'usage': {'input_tokens': 40, 'output_tokens': 4},
            },
        }
    return responses


def result_for(
    case,
    router_id,
    *,
    intent=None,
    action=None,
    authorized=False,
    elapsed_ms=10.0,
    error=None,
    model='test-model',
):
    return bench.CaseResult(
        case_id=case.id,
        router_id=router_id,
        model=model,
        prompt_hash='hash',
        intent=intent if intent is not None else case.expected_intent,
        tier=case.expected_tier,
        complexity=case.expected_complexity,
        action=action,
        authorized=authorized,
        requests=1,
        usage_input=10,
        usage_output=2,
        elapsed_ms=elapsed_ms,
        reason=None,
        error=error,
        raw=None,
    )


def test_fixture_has_the_frozen_24_case_split_and_coverage():
    fixture = bench.load_fixture()
    assert len(fixture.cases) == 24
    assert sum(case.split == 'tuning' for case in fixture.cases) == 8
    assert sum(case.split == 'holdout' for case in fixture.cases) == 16
    assert {case.expected_intent for case in fixture.cases} == {intent.value for intent in Intent}
    assert {case.expected_tier for case in fixture.cases} == {1, 2, 3, 4, 5}
    assert {'ambiguity', 'negation', 'quotation', 'compound'} <= {
        tag for case in fixture.cases for tag in case.tags
    }
    assert bench.holdout_digest(fixture.cases) == fixture.holdout_sha256


def test_changing_a_holdout_case_is_rejected_after_the_freeze():
    fixture = bench.load_fixture()
    data = json.loads((ROOT / 'tests/fixtures/day2_routing_cases.json').read_text())
    for case in data['cases']:
        if case['split'] == 'holdout':
            case['text'] += ' changed'
            break
    # Validate the reconstructed fixture directly; it must not match the frozen digest.
    cases = tuple(
        bench.Case(
            id=raw['id'],
            split=raw['split'],
            text=raw['text'],
            expected_intent=raw['expected']['intent'],
            expected_tier=raw['expected']['tier'],
            expected_complexity=raw['expected']['complexity'],
            expected_action_kind=raw['expected'].get('action_kind'),
            expected_clarification=bool(raw['expected']['clarification']),
            tags=tuple(raw.get('tags', ())),
            rationale=raw.get('rationale', ''),
        )
        for raw in data['cases']
    )
    with pytest.raises(ValueError, match='holdout split changed'):
        bench.validate_fixture(bench.Fixture(
            version=fixture.version,
            frozen_at=fixture.frozen_at,
            holdout_sha256=fixture.holdout_sha256,
            approved_voices=fixture.approved_voices,
            allowed_operation_ids=fixture.allowed_operation_ids,
            cases=cases,
        ))


def test_local_exact_router_only_routes_known_exact_commands():
    fixture = bench.load_fixture()
    router = bench.LocalRouter()
    exact = next(case for case in fixture.cases if case.id == 'tune-02')
    ordinary = next(case for case in fixture.cases if case.id == 'tune-01')

    output = asyncio.run(router.route(exact))
    assert output.intent == 'voice_setting'
    assert output.tier == 1
    assert output.complexity == 'simple'
    assert output.action == {'kind': 'AdjustSpeakingSpeed', 'delta': -0.1}
    assert output.authorized is True
    assert output.requests == 0

    abstained = asyncio.run(router.route(ordinary))
    assert abstained.intent is None
    assert abstained.action is None
    assert abstained.requests == 0


def test_isolated_state_mocks_external_effects_and_never_touches_real_data():
    state = bench.IsolatedActionState()
    changed = state.apply(SetSpeakingSpeed(speed=1.2))
    assert changed['changed'] is True
    assert state.settings.speaking_speed == 1.2
    assert state.memory_queries == []

    state.apply(SearchMemory(query='trip'))
    assert state.memory_queries == ['trip']
    assert state.external_effects == ['memory-read']

    state.apply(ShowConversationHistory())
    assert state.navigation == 'history'
    assert state.external_effects == ['memory-read']


class CountingRouter:
    id = 'counting'
    model = 'fake'
    calls = 0
    fail = False

    def prompt_hash(self, case):
        return 'prompt-' + case.id

    async def route(self, case):
        self.calls += 1
        if self.fail:
            raise RuntimeError('scripted failure')
        return bench.RouterOutput(intent=case.expected_intent, tier=case.expected_tier, requests=1)


def test_cache_resume_avoids_repeating_work_and_preserves_failures(tmp_path):
    fixture = bench.load_fixture()
    cache_path = tmp_path / 'cache.json'
    router = CountingRouter()
    router.fail = False
    results, _ = asyncio.run(bench.run_benchmark(fixture, [router], bench.ResultCache(cache_path)))
    assert len(results) == 24
    assert router.calls == 24

    resume_router = CountingRouter()
    results_again, _ = asyncio.run(bench.run_benchmark(fixture, [resume_router], bench.ResultCache(cache_path)))
    assert resume_router.calls == 0
    assert [result.intent for result in results_again] == [result.intent for result in results]

    failing_router = CountingRouter()
    failing_router.id = 'counting_failure'
    failing_router.fail = True
    failing, _ = asyncio.run(bench.run_benchmark(fixture, [failing_router], bench.ResultCache(cache_path)))
    assert all(result.error == 'EXCEPTION:RuntimeError' for result in failing)
    assert failing_router.calls == 24

    failing_resume = CountingRouter()
    failing_resume.id = 'counting_failure'
    failing_resume.fail = True
    _, report = asyncio.run(bench.run_benchmark(fixture, [failing_resume], bench.ResultCache(cache_path)))
    assert failing_resume.calls == 0
    assert report['routers']['counting_failure']['failure_count'] == 24


def test_cache_key_includes_case_router_model_and_prompt_hash():
    router = CountingRouter()
    key_a = bench.ResultCache.key('case-a', router, 'prompt-a')
    key_b = bench.ResultCache.key('case-a', router, 'prompt-b')
    assert key_a != key_b

    class OtherRouter(CountingRouter):
        model = 'other-model'

    assert bench.ResultCache.key('case-a', router, 'prompt-a') != bench.ResultCache.key(
        'case-a', OtherRouter(), 'prompt-a'
    )


def test_aggregate_records_agreement_unsafe_actions_requests_latency_and_cost():
    fixture = bench.load_fixture()
    cases = fixture.cases[:2]
    first, second = cases
    results = [
        result_for(first, 'jev_assisted', action={'kind': 'SetCaptionsVisible', 'visible': True}, authorized=True, elapsed_ms=100),
        result_for(second, 'jev_assisted', elapsed_ms=120),
    ]
    aggregate = bench.aggregate_results(
        cases,
        results,
        prices={'test-model': bench.Price(1.0, 2.0)},
    )['jev_assisted']
    assert aggregate['sample_size'] == 2
    assert aggregate['intent_agreement'] == 1.0
    assert aggregate['unsafe_action_proposals'] == 1
    assert aggregate['unsafe_authorizations'] == 1
    assert aggregate['requests_issued'] == 2
    assert aggregate['requests_avoided_vs_one_direct_call'] == 0
    assert aggregate['raw_latency_ms'] == [100, 120]
    assert aggregate['median_latency_ms'] == 110
    assert aggregate['input_tokens'] == 20
    assert aggregate['estimated_cost_usd'] == pytest.approx((20 / 1_000_000) + (4 / 1_000_000) * 2)


def test_holdout_gate_passes_only_with_agreement_zero_unsafe_and_latency():
    fixture = bench.load_fixture()
    holdout = [case for case in fixture.cases if case.split == 'holdout']
    jev = [result_for(case, 'jev_assisted', elapsed_ms=100) for case in holdout]
    direct = [result_for(case, 'direct', elapsed_ms=50) for case in holdout]
    passed = bench.holdout_gate(fixture.cases, jev + direct)
    assert passed['passed'] is True
    assert passed['median_added_overhead_ms'] == 50

    wrong = list(jev)
    wrong[0] = result_for(holdout[0], 'jev_assisted', intent='tool_request', elapsed_ms=100)
    wrong[1] = result_for(holdout[1], 'jev_assisted', intent='tool_request', elapsed_ms=100)
    failed = bench.holdout_gate(fixture.cases, wrong + direct)
    assert failed['passed'] is False
    assert 'INTENT_AGREEMENT_BELOW_90_PERCENT' in failed['reasons']

    unsafe = list(jev)
    unsafe[0] = result_for(
        holdout[0], 'jev_assisted',
        action={'kind': 'SetCaptionsVisible', 'visible': True},
        authorized=True, elapsed_ms=100,
    )
    unsafe_gate = bench.holdout_gate(fixture.cases, unsafe + direct)
    assert unsafe_gate['passed'] is False
    assert 'UNSAFE_AUTHORIZATION' in unsafe_gate['reasons']

    slow = [result_for(case, 'jev_assisted', elapsed_ms=400) for case in holdout]
    slow_gate = bench.holdout_gate(fixture.cases, slow + direct)
    assert slow_gate['passed'] is False
    assert 'MEDIAN_OVERHEAD_ABOVE_250MS' in slow_gate['reasons']

    incomplete = bench.holdout_gate(fixture.cases, jev[:-1] + direct)
    assert incomplete['passed'] is False
    assert any(reason.startswith('INCOMPLETE_HOLDOUT') for reason in incomplete['reasons'])


def test_scripted_run_uses_real_validators_caches_and_keeps_routing_off(tmp_path):
    fixture = bench.load_fixture()
    routers = bench.scripted_routers(fixture, script_for(fixture))
    cache_path = tmp_path / 'cache.json'
    results, report = asyncio.run(bench.run_benchmark(fixture, routers, bench.ResultCache(cache_path)))
    assert len(results) == 24 * 3
    assert report['holdout_gate']['passed'] is True
    assert report['automatic_routing_enabled'] is False
    assert report['routers']['jev_assisted']['intent_agreement'] == 1.0
    assert report['routers']['jev_assisted']['unsafe_authorizations'] == 0
    assert cache_path.exists()

    # The second run reads every cached case and does not append duplicate rows.
    cached_rows = json.loads(cache_path.read_text())['entries']
    results_again, _ = asyncio.run(bench.run_benchmark(fixture, routers, bench.ResultCache(cache_path)))
    assert len(results_again) == len(results)
    assert len(json.loads(cache_path.read_text())['entries']) == len(cached_rows)


def test_live_mode_requires_an_explicit_flag_and_injected_module(tmp_path):
    with pytest.raises(SystemExit, match='--live requires --live-module'):
        bench.main(['--live', '--cache', str(tmp_path / 'cache.json')])


def test_default_mode_is_dry_run_and_makes_no_provider_calls(tmp_path, capsys):
    code = bench.main(['--report', str(tmp_path / 'report.json')])
    assert code == 0
    report = json.loads((tmp_path / 'report.json').read_text())
    assert report['mode'] == 'dry_run'
    assert report['routers'] == {}
    assert report['holdout_gate']['reasons'] == ['NOT_EVALUATED']
    assert report['automatic_routing_enabled'] is False
