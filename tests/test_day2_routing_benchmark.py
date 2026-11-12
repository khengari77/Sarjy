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
from app.services.action_extraction import GenerationConfig  # noqa: E402

GENERATION = GenerationConfig(
    model='deepseek-flash',
    max_output_tokens=64,
    input_usd_per_million=0.5,
    output_usd_per_million=1.0,
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


def action_for(case):
    if case.expected_action_kind is None:
        return None
    effect = case.expected_effect or {}
    if case.expected_action_kind == 'AdjustSpeakingSpeed':
        return {'kind': 'AdjustSpeakingSpeed', 'delta': -0.1}
    if case.expected_action_kind == 'SetSpeakingSpeed':
        return {'kind': 'SetSpeakingSpeed', 'speed': effect['settings']['speaking_speed']}
    if case.expected_action_kind == 'SetCaptionsVisible':
        return {'kind': 'SetCaptionsVisible', 'visible': effect['settings']['captions_visible']}
    if case.expected_action_kind == 'SelectApprovedVoice':
        return {'kind': 'SelectApprovedVoice', 'voice_id': 'aurora'}
    if case.expected_action_kind in {'ShowConversationHistory', 'OpenMemoryExplorer'}:
        return {'kind': case.expected_action_kind}
    if case.expected_action_kind == 'SearchMemory':
        return {
            'kind': 'SearchMemory',
            'query': ' '.join(effect.get('memory_query_terms', ['trip'])),
        }
    if case.expected_action_kind == 'ProposeContextExclusion':
        return {
            'kind': 'ProposeContextExclusion',
            'query': ' '.join(effect.get('proposal_terms', ['trip'])),
            'keep': [],
        }
    if case.expected_action_kind == 'RestoreContextOperation':
        return {
            'kind': 'RestoreContextOperation',
            'operation_id': effect.get('restored_operation_id', 'operation-last-exclusion'),
        }
    raise AssertionError(case.expected_action_kind)


def script_for(fixture):
    responses = {}
    for case in fixture.cases:
        action = action_for(case)
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
    mode='live',
    usage=None,
):
    return bench.CaseResult(
        case_id=case.id,
        router_id=router_id,
        model=model,
        prompt_hash='hash',
        mode=mode,
        intent=intent if intent is not None else case.expected_intent,
        tier=case.expected_tier,
        complexity=case.expected_complexity,
        action=action,
        authorized=authorized,
        requests=1,
        usage=usage if usage is not None else [
            {'provider': 'test', 'model': model, 'purpose': 'test', 'input_tokens': 10, 'output_tokens': 2}
        ],
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
    assert all(
        (case.expected_action_kind is None) == (case.expected_effect is None)
        for case in fixture.cases
    )
    assert all(
        case.expected_effect
        for case in fixture.cases if case.expected_action_kind is not None
    )


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
            expected_effect=raw['expected'].get('effect'),
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
    provenance = 'scripted'
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
    results, _ = asyncio.run(bench.run_benchmark(fixture, [router], bench.ResultCache(cache_path, 'scripted'), mode='scripted'))
    assert len(results) == 24
    assert router.calls == 24

    resume_router = CountingRouter()
    results_again, _ = asyncio.run(bench.run_benchmark(fixture, [resume_router], bench.ResultCache(cache_path, 'scripted'), mode='scripted'))
    assert resume_router.calls == 0
    assert [result.intent for result in results_again] == [result.intent for result in results]

    failing_router = CountingRouter()
    failing_router.id = 'counting_failure'
    failing_router.fail = True
    failing, _ = asyncio.run(bench.run_benchmark(fixture, [failing_router], bench.ResultCache(cache_path, 'scripted'), mode='scripted'))
    assert all(result.error == 'EXCEPTION:RuntimeError' for result in failing)
    assert failing_router.calls == 24

    failing_resume = CountingRouter()
    failing_resume.id = 'counting_failure'
    failing_resume.fail = True
    _, report = asyncio.run(bench.run_benchmark(fixture, [failing_resume], bench.ResultCache(cache_path, 'scripted'), mode='scripted'))
    assert failing_resume.calls == 0
    assert report['routers']['counting_failure']['failure_count'] == 24


def test_cache_key_includes_mode_and_provenance():
    router = CountingRouter()

    class LiveRouter(CountingRouter):
        provenance = 'live'

    assert bench.ResultCache.key('case-a', router, 'prompt-a', 'scripted') != bench.ResultCache.key(
        'case-a', LiveRouter(), 'prompt-a', 'scripted'
    )


def test_extraction_generation_config_is_part_of_the_cache_identity():
    fixture = bench.load_fixture()
    script = script_for(fixture)
    case = fixture.cases[0]
    base = bench.ScriptedJevRouter(fixture, script, GENERATION)
    changed_model = bench.ScriptedJevRouter(fixture, script, GenerationConfig(
        model='other-extractor',
        max_output_tokens=GENERATION.max_output_tokens,
        input_usd_per_million=GENERATION.input_usd_per_million,
        output_usd_per_million=GENERATION.output_usd_per_million,
    ))
    changed_bound = bench.ScriptedJevRouter(fixture, script, GenerationConfig(
        model=GENERATION.model,
        max_output_tokens=32,
        input_usd_per_million=GENERATION.input_usd_per_million,
        output_usd_per_million=GENERATION.output_usd_per_million,
    ))
    changed_prices = bench.ScriptedJevRouter(fixture, script, GenerationConfig(
        model=GENERATION.model,
        max_output_tokens=GENERATION.max_output_tokens,
        input_usd_per_million=2.0,
        output_usd_per_million=3.0,
    ))
    hashes = {
        base.prompt_hash(case),
        changed_model.prompt_hash(case),
        changed_bound.prompt_hash(case),
        changed_prices.prompt_hash(case),
    }
    assert len(hashes) == 4
    keys = {
        bench.ResultCache.key(case.id, router, router.prompt_hash(case), 'scripted')
        for router in (base, changed_model, changed_bound, changed_prices)
    }
    assert len(keys) == 4


def test_scripted_cache_is_rejected_for_a_live_run(tmp_path):
    path = tmp_path / 'cache.json'
    path.write_text(json.dumps({'schema_version': 3, 'mode': 'scripted', 'entries': {}}))
    with pytest.raises(ValueError, match='provenance mismatch'):
        bench.ResultCache(path, 'live')

    fixture = bench.load_fixture()
    cache = bench.ResultCache(tmp_path / 'live.json', 'live')
    with pytest.raises(ValueError, match='live-provenance'):
        asyncio.run(bench.run_benchmark(fixture, [CountingRouter()], cache, mode='live'))


def test_cache_key_includes_case_router_model_and_prompt_hash():
    router = CountingRouter()
    key_a = bench.ResultCache.key('case-a', router, 'prompt-a', 'scripted')
    key_b = bench.ResultCache.key('case-a', router, 'prompt-b', 'scripted')
    assert key_a != key_b

    class OtherRouter(CountingRouter):
        model = 'other-model'

    assert bench.ResultCache.key('case-a', router, 'prompt-a', 'scripted') != bench.ResultCache.key(
        'case-a', OtherRouter(), 'prompt-a', 'scripted'
    )
    assert bench.ResultCache.key('case-a', router, 'prompt-a', 'scripted') != bench.ResultCache.key(
        'case-a', router, 'prompt-a', 'live'
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
    assert aggregate['usage_by_model']['test-model'] == {'input_tokens': 20, 'output_tokens': 4}
    assert aggregate['cost_by_model']['test-model'] == pytest.approx((20 / 1_000_000) + (4 / 1_000_000) * 2)
    assert aggregate['estimated_cost_usd'] == pytest.approx((20 / 1_000_000) + (4 / 1_000_000) * 2)


def test_wrong_arguments_are_unsafe_authorizations():
    fixture = bench.load_fixture()
    holdout = [case for case in fixture.cases if case.split == 'holdout']
    target = next(case for case in holdout if case.id == 'holdout-04')
    jev = [
        result_for(
            case,
            'jev_assisted',
            action={'kind': 'SetSpeakingSpeed', 'speed': 1.1} if case.id == target.id else action_for(case),
            authorized=True,
            elapsed_ms=100,
        )
        for case in holdout
    ]
    direct = [result_for(case, 'direct', elapsed_ms=50) for case in holdout]
    evaluated = bench.evaluate_result(target, next(result for result in jev if result.case_id == target.id))
    assert evaluated['effect_match'] is False
    assert evaluated['unsafe_authorization'] is True
    gate = bench.holdout_gate(fixture.cases, jev + direct, mode='live')
    assert gate['passed'] is False
    assert 'UNSAFE_AUTHORIZATION' in gate['reasons']


def test_usage_and_cost_are_calculated_per_model():
    fixture = bench.load_fixture()
    case = fixture.cases[0]
    result = result_for(
        case,
        'jev_assisted',
        usage=[
            {'provider': 'jev', 'model': 'jev-1.14.0', 'purpose': 'classification', 'input_tokens': 1000, 'output_tokens': 0},
            {'provider': 'deepseek', 'model': 'deepseek-flash', 'purpose': 'extraction', 'input_tokens': 1000, 'output_tokens': 100},
        ],
    )
    prices = {
        'jev-1.14.0': bench.Price(0.042, 0.0),
        'deepseek-flash': bench.Price(0.028, 0.042),
    }
    aggregate = bench.aggregate_results([case], [result], prices=prices)['jev_assisted']
    assert aggregate['usage_by_model']['jev-1.14.0'] == {'input_tokens': 1000, 'output_tokens': 0}
    assert aggregate['usage_by_model']['deepseek-flash'] == {'input_tokens': 1000, 'output_tokens': 100}
    assert aggregate['cost_by_model']['jev-1.14.0'] == pytest.approx(0.000042)
    assert aggregate['cost_by_model']['deepseek-flash'] == pytest.approx(0.000028 + 0.0000042)
    assert aggregate['estimated_cost_usd'] == pytest.approx(0.000042 + 0.000028 + 0.0000042)

    unpriced = bench.aggregate_results([case], [result], prices={'jev-1.14.0': bench.Price(0.042, 0.0)})['jev_assisted']
    assert unpriced['estimated_cost_usd'] is None
    assert unpriced['unpriced_models'] == ['deepseek-flash']


def test_holdout_gate_passes_only_with_agreement_zero_unsafe_and_latency():
    fixture = bench.load_fixture()
    holdout = [case for case in fixture.cases if case.split == 'holdout']
    jev = [result_for(case, 'jev_assisted', elapsed_ms=100) for case in holdout]
    direct = [result_for(case, 'direct', elapsed_ms=50) for case in holdout]
    passed = bench.holdout_gate(fixture.cases, jev + direct, mode='live')
    assert passed['passed'] is True
    assert passed['median_added_overhead_ms'] == 50

    wrong = list(jev)
    wrong[0] = result_for(holdout[0], 'jev_assisted', intent='tool_request', elapsed_ms=100)
    wrong[1] = result_for(holdout[1], 'jev_assisted', intent='tool_request', elapsed_ms=100)
    failed = bench.holdout_gate(fixture.cases, wrong + direct, mode='live')
    assert failed['passed'] is False
    assert 'INTENT_AGREEMENT_BELOW_90_PERCENT' in failed['reasons']

    unsafe = list(jev)
    unsafe[0] = result_for(
        holdout[0], 'jev_assisted',
        action={'kind': 'SetCaptionsVisible', 'visible': True},
        authorized=True, elapsed_ms=100,
    )
    unsafe_gate = bench.holdout_gate(fixture.cases, unsafe + direct, mode='live')
    assert unsafe_gate['passed'] is False
    assert 'UNSAFE_AUTHORIZATION' in unsafe_gate['reasons']

    slow = [result_for(case, 'jev_assisted', elapsed_ms=400) for case in holdout]
    slow_gate = bench.holdout_gate(fixture.cases, slow + direct, mode='live')
    assert slow_gate['passed'] is False
    assert 'MEDIAN_OVERHEAD_ABOVE_250MS' in slow_gate['reasons']

    incomplete = bench.holdout_gate(fixture.cases, jev[:-1] + direct, mode='live')
    assert incomplete['passed'] is False
    assert any(reason.startswith('INCOMPLETE_HOLDOUT') for reason in incomplete['reasons'])

    incomplete_direct = bench.holdout_gate(fixture.cases, jev + direct[:1], mode='live')
    assert incomplete_direct['passed'] is False
    assert any(reason.startswith('INCOMPLETE_DIRECT_RESULTS') for reason in incomplete_direct['reasons'])
    assert any(reason.startswith('INCOMPLETE_PAIRED_LATENCY') for reason in incomplete_direct['reasons'])


def test_scripted_run_uses_real_validators_caches_and_keeps_routing_off(tmp_path):
    fixture = bench.load_fixture()
    routers = bench.scripted_routers(fixture, script_for(fixture), GENERATION)
    cache_path = tmp_path / 'cache.json'
    results, report = asyncio.run(bench.run_benchmark(
        fixture, routers, bench.ResultCache(cache_path, 'scripted'), mode='scripted'
    ))
    assert len(results) == 24 * 3
    assert report['holdout_gate']['passed'] is False
    assert 'NOT_LIVE' in report['holdout_gate']['reasons']
    assert report['automatic_routing_enabled'] is False
    assert report['routers']['jev_assisted']['intent_agreement'] == 1.0
    assert report['routers']['jev_assisted']['unsafe_authorizations'] == 0
    assert cache_path.exists()

    # The second run reads every cached case and does not append duplicate rows.
    cached_rows = json.loads(cache_path.read_text())['entries']
    results_again, _ = asyncio.run(bench.run_benchmark(
        fixture, routers, bench.ResultCache(cache_path, 'scripted'), mode='scripted'
    ))
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
