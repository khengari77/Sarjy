"""Memory policy laws: the model proposes, this pure function decides."""

import pytest
from app.domain.commands import parse_exact_command
from app.domain.contracts import SetMemoryMode, UserSettings, apply_settings_action
from app.domain.memory_policy import Decision, Sensitivity, classify_sensitivity, combine, decide
from hypothesis import given
from hypothesis import strategies as st

MODES = st.sampled_from(["off", "ask", "auto"])
SENSITIVITIES = st.sampled_from(list(Sensitivity))
ORDER = [
    Sensitivity.NORMAL,
    Sensitivity.THIRD_PARTY,
    Sensitivity.HEALTH,
    Sensitivity.FINANCE,
    Sensitivity.CREDENTIAL,
]


@given(MODES, st.booleans())
def test_a_credential_is_never_saved_in_any_mode(mode, explicit):
    assert decide(mode, Sensitivity.CREDENTIAL, explicit=explicit) is Decision.SKIP


@given(SENSITIVITIES, st.booleans())
def test_off_never_saves_or_asks(sensitivity, explicit):
    assert decide("off", sensitivity, explicit=explicit) is Decision.SKIP


@given(SENSITIVITIES, st.booleans())
def test_ask_never_saves_without_a_question(sensitivity, explicit):
    assert decide("ask", sensitivity, explicit=explicit) is not Decision.SAVE


@given(SENSITIVITIES)
def test_an_explicit_request_never_makes_the_outcome_stricter(sensitivity):
    order = [Decision.SKIP, Decision.ASK, Decision.SAVE]
    for mode in ("off", "ask", "auto"):
        assert order.index(decide(mode, sensitivity, explicit=True)) >= order.index(
            decide(mode, sensitivity, explicit=False)
        )


@given(SENSITIVITIES, st.booleans())
def test_a_more_permissive_mode_never_turns_save_into_skip(sensitivity, explicit):
    permissiveness = {"off": 0, "ask": 1, "auto": 2}
    rank = {Decision.SKIP: 0, Decision.ASK: 1, Decision.SAVE: 2}
    # ask is stricter than auto for ordinary facts, but never stricter than skipping them.
    assert rank[decide("auto", sensitivity, explicit=explicit)] >= rank[decide("off", sensitivity, explicit=explicit)]
    assert permissiveness["auto"] > permissiveness["off"]


@given(st.lists(SENSITIVITIES, min_size=1, max_size=4))
def test_combine_is_the_most_sensitive_label(labels):
    assert combine(*labels) is max(labels, key=ORDER.index)


@given(SENSITIVITIES, SENSITIVITIES)
def test_the_keyword_net_can_only_raise_sensitivity(model_label, net_label):
    # combine(model, net) is never below either input, so a model that says "normal" cannot lower a keyword hit.
    combined = combine(model_label, net_label)
    assert ORDER.index(combined) >= ORDER.index(model_label)
    assert ORDER.index(combined) >= ORDER.index(net_label)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my password is hunter2", Sensitivity.CREDENTIAL),
        ("my pin code is 1234", Sensitivity.CREDENTIAL),
        ("my api key is abc", Sensitivity.CREDENTIAL),
        ("my openai token is sk-proj-abcd1234", Sensitivity.CREDENTIAL),
        ("value sk-proj-abcd1234", Sensitivity.CREDENTIAL),
        ("my github key is ghp_abcdEFGH12345678", Sensitivity.CREDENTIAL),
        ("the bucket key is AKIAABCDEFGH12345678", Sensitivity.CREDENTIAL),
        ("the slack hook is xoxb-not-a-real-token", Sensitivity.CREDENTIAL),
        ("the jwt is eyJhbGciOiJIUzI1NiJ9.payload", Sensitivity.CREDENTIAL),
        ("وكلمة المرور هي hunter2", Sensitivity.CREDENTIAL),
        ("بالرقم السري 8877", Sensitivity.CREDENTIAL),
        ("ورمز التحقق 4567", Sensitivity.CREDENTIAL),
        ("رقم الهوية 1234", Sensitivity.CREDENTIAL),
        ("رقم البطاقة 4567", Sensitivity.CREDENTIAL),
        ("I make a salary of ninety thousand", Sensitivity.FINANCE),
        ("أريد بطاقة ائتمان جديدة", Sensitivity.FINANCE),
        ("my doctor put me on metformin", Sensitivity.HEALTH),
        ("my boss is called Sam", Sensitivity.THIRD_PARTY),
        ("my favorite color is blue", Sensitivity.NORMAL),
        ("the token budget is low", Sensitivity.NORMAL),
        ("I live in Benghazi", Sensitivity.NORMAL),
        ("رقم الهوية", Sensitivity.NORMAL),
        ("رقم البطاقة", Sensitivity.NORMAL),
    ],
)
def test_classifier_examples(text, expected):
    assert classify_sensitivity(text) is expected


def test_decisions_for_the_demo_script():
    assert decide("auto", classify_sensitivity("my favorite color is blue"), explicit=True) is Decision.SAVE
    assert decide("auto", classify_sensitivity("my doctor put me on metformin"), explicit=False) is Decision.ASK
    assert decide("auto", classify_sensitivity("my password is hunter2"), explicit=True) is Decision.SKIP
    assert decide("auto", classify_sensitivity("my openai token is sk-proj-abcd1234"), explicit=True) is Decision.SKIP
    assert decide("auto", classify_sensitivity("أريد بطاقة ائتمان جديدة"), explicit=True) is Decision.ASK
    assert decide("ask", classify_sensitivity("my favorite color is blue"), explicit=True) is Decision.ASK
    assert decide("off", classify_sensitivity("my favorite color is blue"), explicit=True) is Decision.SKIP


@pytest.mark.parametrize(
    ("text", "mode", "scope"),
    [
        ("turn memory off", "off", "chat"),
        ("Turn off memory.", "off", "chat"),
        ("don't save anything in this chat", "off", "chat"),
        ("ask before remembering", "ask", "chat"),
        ("Can you ask me before remembering?", "ask", "chat"),
        ("remember things automatically", "auto", "chat"),
        ("turn memory on, please", "auto", "chat"),
        ("turn memory off by default", "off", "account"),
        ("always keep memory off", "off", "account"),
        ("turn memory off in my settings", "off", "account"),
        ("remember things automatically for my account", "auto", "account"),
    ],
)
def test_memory_mode_phrases_parse_to_the_action(text, mode, scope):
    assert parse_exact_command(text) == SetMemoryMode(mode=mode, scope=scope)


def test_clearing_the_chat_override_is_a_chat_scope_action():
    assert parse_exact_command("go back to my default memory setting") == SetMemoryMode(mode=None, scope="chat")


@pytest.mark.parametrize(
    "text",
    [
        "I want to turn memory off for my diary",
        "memory off topic",
        "do you have memory on this phone",
        "remember things automatically is a good feature",
        "don't remember anything",
        "I'd turn memory on if it were free",
    ],
)
def test_near_misses_are_not_memory_mode_commands(text):
    assert parse_exact_command(text) is None


def test_setting_the_same_mode_is_unchanged_and_a_change_bumps_the_revision():
    settings = UserSettings()
    assert apply_settings_action(settings, SetMemoryMode(mode="auto")) is settings
    changed = apply_settings_action(settings, SetMemoryMode(mode="off"))
    assert (changed.memory_mode, changed.revision) == ("off", settings.revision + 1)
    assert apply_settings_action(changed, SetMemoryMode(mode="off")) is changed


def test_auto_mode_matches_its_documented_table():
    table = {
        (Sensitivity.NORMAL, False): Decision.SAVE,
        (Sensitivity.NORMAL, True): Decision.SAVE,
        (Sensitivity.THIRD_PARTY, False): Decision.ASK,
        (Sensitivity.THIRD_PARTY, True): Decision.SAVE,
        (Sensitivity.HEALTH, False): Decision.ASK,
        (Sensitivity.HEALTH, True): Decision.ASK,
        (Sensitivity.FINANCE, False): Decision.ASK,
        (Sensitivity.FINANCE, True): Decision.ASK,
        (Sensitivity.CREDENTIAL, False): Decision.SKIP,
        (Sensitivity.CREDENTIAL, True): Decision.SKIP,
    }
    for (sensitivity, explicit), expected in table.items():
        assert decide("auto", sensitivity, explicit=explicit) is expected, (sensitivity, explicit)
