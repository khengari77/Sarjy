"""Memory observer laws: the model answers, this pure core decides."""

import pytest
from app.domain.memory_observer import (
    OBSERVER_QUESTIONS,
    ObserverAnswers,
    build_observer_request,
    observer_plan,
    parse_observer_response,
)
from app.domain.memory_policy import Decision, Sensitivity
from app.domain.routing import JEV_MODEL
from app.domain.ui_routing import CONFIDENCE_THRESHOLD
from hypothesis import given
from hypothesis import strategies as st

MODES = st.sampled_from(["off", "ask", "auto"])
SENSITIVITIES = st.sampled_from(list(Sensitivity))
CONFIDENCES = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
CONFIDENT = st.floats(min_value=CONFIDENCE_THRESHOLD, max_value=1.0, allow_nan=False, allow_infinity=False)
ORDER = [
    Sensitivity.NORMAL,
    Sensitivity.THIRD_PARTY,
    Sensitivity.HEALTH,
    Sensitivity.FINANCE,
    Sensitivity.CREDENTIAL,
]
JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True, allow_infinity=True) | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=20,
)


def make_answers(
    *,
    has_fact: bool = True,
    tier: Sensitivity = Sensitivity.NORMAL,
    negated: bool = False,
    confidence: float = 0.99,
) -> ObserverAnswers:
    return ObserverAnswers(has_fact=has_fact, tier=tier, negated=negated, confidence=confidence)


def answer(choice: str, probability: float = 0.9) -> dict[str, object]:
    return {"type": "choice", "choice": choice, "confidence": probability}


def payload() -> dict[str, object]:
    return {
        "model": JEV_MODEL,
        "answers": {
            "fact": answer("yes"),
            "tier": answer("normal"),
            "negation": answer("no"),
        },
    }


# Decisions ---------------------------------------------------------------------------------------------------------


@given(MODES, st.booleans(), SENSITIVITIES, CONFIDENCES)
def test_a_credential_at_the_floor_is_never_saved_or_asked(mode, explicit, tier, confidence):
    answers = make_answers(tier=tier, confidence=confidence)
    assert observer_plan(mode, Sensitivity.CREDENTIAL, answers, explicit=explicit) is Decision.SKIP


@given(MODES, st.booleans(), SENSITIVITIES, CONFIDENCES)
def test_a_credential_from_the_model_is_never_saved_or_asked(mode, explicit, floor, confidence):
    answers = make_answers(tier=Sensitivity.CREDENTIAL, confidence=confidence)
    assert observer_plan(mode, floor, answers, explicit=explicit) is Decision.SKIP


@given(MODES, SENSITIVITIES, SENSITIVITIES, SENSITIVITIES, st.booleans(), st.booleans(), CONFIDENCES)
def test_raising_the_model_tier_never_turns_ask_or_skip_into_save(
    mode, floor, tier, bump, negated, explicit, confidence
):
    raised_tier = max(tier, bump, key=ORDER.index)
    base = observer_plan(
        mode, floor, make_answers(tier=tier, negated=negated, confidence=confidence), explicit=explicit
    )
    raised = observer_plan(
        mode, floor, make_answers(tier=raised_tier, negated=negated, confidence=confidence), explicit=explicit
    )
    if base is not Decision.SAVE:
        assert raised is not Decision.SAVE


@given(MODES, SENSITIVITIES, SENSITIVITIES, st.booleans(), st.booleans(), CONFIDENCES)
def test_a_negated_fact_is_never_saved(mode, floor, tier, explicit, has_fact, confidence):
    answers = make_answers(has_fact=has_fact, tier=tier, negated=True, confidence=confidence)
    assert observer_plan(mode, floor, answers, explicit=explicit) is not Decision.SAVE


@given(
    MODES,
    SENSITIVITIES,
    SENSITIVITIES,
    st.booleans(),
    st.booleans(),
    st.floats(min_value=0.0, max_value=CONFIDENCE_THRESHOLD, exclude_max=True),
)
def test_confidence_below_the_threshold_is_never_saved(mode, floor, tier, explicit, negated, confidence):
    answers = make_answers(tier=tier, negated=negated, confidence=confidence)
    assert observer_plan(mode, floor, answers, explicit=explicit) is not Decision.SAVE


@given(
    st.sampled_from(["off", "ask"]),
    SENSITIVITIES,
    SENSITIVITIES,
    st.booleans(),
    st.booleans(),
    CONFIDENCES,
)
def test_off_and_ask_modes_always_skip(mode, floor, tier, negated, explicit, confidence):
    answers = make_answers(tier=tier, negated=negated, confidence=confidence)
    assert observer_plan(mode, floor, answers, explicit=explicit) is Decision.SKIP
    assert observer_plan(mode, floor, None, explicit=explicit) is Decision.SKIP


@given(MODES, SENSITIVITIES, SENSITIVITIES, st.booleans(), st.booleans(), CONFIDENCES)
def test_no_fact_skips(mode, floor, tier, negated, explicit, confidence):
    answers = make_answers(has_fact=False, tier=tier, negated=negated, confidence=confidence)
    assert observer_plan(mode, floor, answers, explicit=explicit) is Decision.SKIP
    assert observer_plan(mode, floor, None, explicit=explicit) is Decision.SKIP


@given(MODES, SENSITIVITIES, SENSITIVITIES, st.booleans(), st.booleans(), CONFIDENCES)
def test_the_plan_is_deterministic(mode, floor, tier, negated, explicit, confidence):
    answers = make_answers(tier=tier, negated=negated, confidence=confidence)
    assert observer_plan(mode, floor, answers, explicit=explicit) is observer_plan(
        mode, floor, answers, explicit=explicit
    )


@given(CONFIDENT, st.booleans())
def test_an_ordinary_confident_fact_saves_in_auto_mode(confidence, explicit):
    answers = make_answers(tier=Sensitivity.NORMAL, confidence=confidence)
    assert observer_plan("auto", Sensitivity.NORMAL, answers, explicit=explicit) is Decision.SAVE


def test_the_model_can_raise_the_floor_but_not_lower_it():
    answers = make_answers(tier=Sensitivity.HEALTH, confidence=0.99)
    assert observer_plan("auto", Sensitivity.NORMAL, answers, explicit=True) is Decision.ASK
    assert observer_plan("auto", Sensitivity.THIRD_PARTY, answers, explicit=True) is Decision.ASK


# Parsing -----------------------------------------------------------------------------------------------------------


def test_valid_payload_maps_the_choices_and_takes_the_minimum_probability():
    body = {
        "model": JEV_MODEL,
        "answers": {
            "fact": answer("yes", 0.91),
            "tier": answer("health", 0.82),
            "negation": answer("no", 0.97),
        },
    }
    assert parse_observer_response(body) == ObserverAnswers(
        has_fact=True, tier=Sensitivity.HEALTH, negated=False, confidence=0.82
    )


def test_extra_keys_are_ignored_and_do_not_crash():
    body = payload()
    body["usage"] = {"nested": [None, {"pid": 1.5}]}
    answers = body["answers"]
    assert isinstance(answers, dict)
    assert isinstance(answers["fact"], dict)
    answers["fact"]["scores"] = {"yes": [0.9, None]}
    assert parse_observer_response(body) == ObserverAnswers(
        has_fact=True, tier=Sensitivity.NORMAL, negated=False, confidence=0.9
    )


MALFORMED = [
    None,
    "not json",
    ["answers"],
    {},
    {"answers": None},
    {"answers": []},
    {"answers": {}},
    {"answers": {"fact": answer("yes"), "tier": answer("normal")}},
    {"answers": {"fact": answer("yes"), "negation": answer("no")}},
    {"answers": {"tier": answer("normal"), "negation": answer("no")}},
    {"answers": {"fact": "yes", "tier": answer("normal"), "negation": answer("no")}},
    {"answers": {"fact": answer("yes"), "tier": None, "negation": answer("no")}},
    {"answers": {"fact": answer("yes"), "tier": answer("normal"), "negation": ["no"]}},
    {"answers": {"fact": {"type": "choice", "choice": "yes"}, "tier": answer("normal"), "negation": answer("no")}},
    {"answers": {"fact": {"type": "choice", "confidence": 0.9}, "tier": answer("normal"), "negation": answer("no")}},
    {
        "answers": {
            "fact": {"type": "choice", "choice": "yes", "confidence": {"nested": None}},
            "tier": answer("normal"),
            "negation": answer("no"),
        }
    },
    {
        "answers": {
            "fact": {"type": "choice", "choice": {"nested": None}, "confidence": 0.9},
            "tier": answer("normal"),
            "negation": answer("no"),
        }
    },
    {"answers": {"fact": answer(True), "tier": answer("normal"), "negation": answer("no")}},
]


@pytest.mark.parametrize("body", MALFORMED)
def test_malformed_payloads_return_none(body):
    assert parse_observer_response(body) is None


@pytest.mark.parametrize(
    ("key", "choice"),
    [
        ("fact", "maybe"),
        ("fact", "YES"),
        ("tier", "secret"),
        ("tier", "Normal"),
        ("negation", "nooo"),
        ("negation", ""),
    ],
)
def test_unknown_choices_return_none(key, choice):
    body = payload()
    answers = body["answers"]
    assert isinstance(answers, dict)
    answers[key] = answer(choice)
    assert parse_observer_response(body) is None


@pytest.mark.parametrize("probability", [1.5, -0.1, float("nan"), float("inf"), True, "0.9", None])
def test_invalid_probabilities_return_none(probability):
    body = payload()
    answers = body["answers"]
    assert isinstance(answers, dict)
    answers["tier"] = answer("normal", probability)
    assert parse_observer_response(body) is None


@given(JSON_VALUES)
def test_arbitrary_json_never_raises(value):
    result = parse_observer_response(value)
    assert result is None or isinstance(result, ObserverAnswers)


def test_answers_reject_nan_and_out_of_range_confidence():
    for confidence in (float("nan"), -0.01, 1.01, float("inf")):
        with pytest.raises(ValueError):
            ObserverAnswers(has_fact=True, tier=Sensitivity.NORMAL, negated=False, confidence=confidence)


# Request -----------------------------------------------------------------------------------------------------------


def test_build_observer_request_returns_a_fresh_copy_with_exactly_three_questions():
    first = build_observer_request("I live in Benghazi")
    second = build_observer_request("I live in Benghazi")
    assert first["model"] == JEV_MODEL
    assert first["state"]["current_instruction"] == "I live in Benghazi"
    assert set(first["questions"]) == {"fact", "tier", "negation"}
    assert first["questions"] == OBSERVER_QUESTIONS
    assert first["questions"] is not OBSERVER_QUESTIONS
    assert first["questions"] is not second["questions"]
    first["questions"]["fact"]["criteria"]["yes"] = "changed"
    assert second["questions"] == OBSERVER_QUESTIONS
    assert OBSERVER_QUESTIONS["fact"]["criteria"]["yes"] != "changed"


def test_the_real_jev_wire_shape_parses():
    body = {
        "model": JEV_MODEL,
        "answers": {
            "fact": {"type": "choice", "choice": "yes", "confidence": 0.99, "probabilities": {"yes": 0.96, "no": 0.04}},
            "tier": {"type": "choice", "choice": "normal", "confidence": 0.9, "probabilities": {"normal": 0.93}},
            "negation": {"type": "choice", "choice": "no", "confidence": 0.9},
        },
    }
    assert parse_observer_response(body) == ObserverAnswers(
        has_fact=True, tier=Sensitivity.NORMAL, negated=False, confidence=0.9
    )


def test_a_huge_integer_probability_returns_none_instead_of_raising():
    body = payload()
    body["answers"]["fact"]["confidence"] = 10**400  # type: ignore[index]
    assert parse_observer_response(body) is None


def test_a_wrong_model_or_missing_type_is_rejected():
    body = payload()
    body["model"] = "other"
    assert parse_observer_response(body) is None
    body = payload()
    del body["answers"]["fact"]["type"]  # type: ignore[attr-defined]
    assert parse_observer_response(body) is None


@pytest.mark.parametrize("floor", [Sensitivity.HEALTH, Sensitivity.FINANCE, Sensitivity.THIRD_PARTY])
def test_a_model_saying_normal_never_lowers_the_floor(floor):
    assert observer_plan(
        "auto", floor, make_answers(tier=Sensitivity.NORMAL), explicit=floor is Sensitivity.THIRD_PARTY
    ) is (Decision.SAVE if floor is Sensitivity.THIRD_PARTY else Decision.ASK)


def test_a_retraction_of_a_real_fact_asks():
    assert observer_plan("auto", Sensitivity.NORMAL, make_answers(negated=True), explicit=True) is Decision.ASK


def test_an_overlong_instruction_is_refused():
    with pytest.raises(ValueError):
        build_observer_request("x" * 5000)


def test_a_retraction_with_no_fact_is_left_to_the_voice_commands():
    # "Forget that I like tea" is an instruction to the assistant, not a fact; the command path owns it.
    answers = make_answers(has_fact=False, negated=True)
    assert observer_plan("auto", Sensitivity.NORMAL, answers, explicit=True) is Decision.SKIP


def test_the_chosen_option_probability_beats_the_vendor_confidence():
    body = payload()
    body["answers"]["fact"] = {  # type: ignore[index]
        "type": "choice",
        "choice": "yes",
        "confidence": 0.99,
        "probabilities": {"yes": 0.55, "no": 0.45},
    }
    parsed = parse_observer_response(body)
    assert parsed is not None and parsed.confidence == 0.55
    assert observer_plan("auto", Sensitivity.NORMAL, parsed, explicit=True) is Decision.ASK
