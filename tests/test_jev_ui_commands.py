"""Jev UI-command classification (#168): evaluator, client and finalized-utterance seam."""

import asyncio
import json

import httpx
import pytest
from app.config import Settings
from app.domain.contracts import AdjustSpeakingSpeed, SetCaptionsVisible, SetTheme
from app.domain.routing import JEV_MODEL
from app.domain.ui_routing import (
    CONFIDENCE_THRESHOLD,
    QUESTION_OPTIONS,
    UiActionDecision,
    UiAnswer,
    UiClarificationDecision,
    UiClassification,
    UiVoiceGenderDecision,
    build_ui_request,
    evaluate_ui_classification,
    parse_ui_response,
    ui_text_is_eligible,
)
from app.infrastructure.database import Base
from app.main import create_app
from app.services.budget import reserve_jev_request
from app.services.jev_ui import JevUiClient
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

HIGH = 0.97


def classification(
    route="ui_command",
    family="none",
    theme="none",
    captions="none",
    speed="none",
    voice="none",
    memory_op="none",
    p=HIGH,
):
    def answer(choice, probability=p):
        return UiAnswer(choice=choice, probability=probability)

    return UiClassification(
        route=answer(route),
        family=answer(family),
        theme=answer(theme),
        captions=answer(captions),
        speed=answer(speed),
        voice=answer(voice),
        memory_op=answer(memory_op),
    )


def payload(**choices):
    base = {
        "route": "ui_command",
        "family": "none",
        "theme": "none",
        "captions": "none",
        "speed": "none",
        "voice": "none",
        "memory_op": "none",
    }
    base.update(choices)
    answers = {}
    for name, selected in base.items():
        options = QUESTION_OPTIONS[name]
        rest = [o for o in options if o != selected]
        probabilities = {selected: 0.96, **{o: 0.04 / len(rest) for o in rest}}
        answers[name] = {"type": "choice", "choice": selected, "confidence": 0.9, "probabilities": probabilities}
    return {"model": JEV_MODEL, "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 1}}


# Evaluator ---------------------------------------------------------------------------------------------------------

TABLE = [
    (classification(family="theme", theme="dark"), UiActionDecision(action=SetTheme(theme="dark"))),
    (classification(family="theme", theme="light"), UiActionDecision(action=SetTheme(theme="light"))),
    (classification(family="theme", theme="system"), UiActionDecision(action=SetTheme(theme="system"))),
    (classification(family="captions", captions="show"), UiActionDecision(action=SetCaptionsVisible(visible=True))),
    (classification(family="captions", captions="hide"), UiActionDecision(action=SetCaptionsVisible(visible=False))),
    (classification(family="speed", speed="faster"), UiActionDecision(action=AdjustSpeakingSpeed(delta=0.1))),
    (classification(family="speed", speed="slower"), UiActionDecision(action=AdjustSpeakingSpeed(delta=-0.1))),
    (classification(family="voice", voice="female"), UiVoiceGenderDecision(gender="female")),
    (classification(family="voice", voice="male"), UiVoiceGenderDecision(gender="male")),
    (classification(route="multiple_commands", family="theme", theme="dark"), UiClarificationDecision()),
    (classification(route="conversation", family="theme", theme="dark"), None),  # confident chat vetoes
    (classification(family="none", theme="dark"), None),
    (classification(family="theme", theme="none"), None),
    (classification(family="theme", captions="show"), None),
    (classification(family="captions", theme="dark", captions="none"), None),
    (classification(family="voice", voice="named"), None),
    (classification(family="voice", voice="other"), None),
    (classification(family="speed", speed="none"), None),
]


@pytest.mark.parametrize(("result", "expected"), TABLE)
def test_evaluator_table(result, expected):
    assert evaluate_ui_classification(result) == expected


def test_threshold_is_inclusive_and_family_and_detail_gate_independently():
    ok = {"family": "theme", "theme": "dark"}
    assert evaluate_ui_classification(classification(p=CONFIDENCE_THRESHOLD, **ok)) is not None
    low = CONFIDENCE_THRESHOLD - 0.01
    for level in ("family", "theme"):
        built = classification(**ok).model_dump()
        built[level]["probability"] = low
        assert evaluate_ui_classification(UiClassification(**built)) is None
    weak_route = classification(**ok).model_dump()
    weak_route["route"]["probability"] = low
    assert evaluate_ui_classification(UiClassification(**weak_route)) is not None
    clarification = classification(route="multiple_commands").model_dump()
    clarification["route"]["probability"] = low
    assert evaluate_ui_classification(UiClassification(**clarification)) is None


def recorded(route, family, detail, memory_op="none"):
    """Build a classification from (choice, probability) pairs as recorded live."""

    def answer(pair):
        return UiAnswer(choice=pair[0], probability=pair[1])

    none = ("none", 0.9)
    detail_name, detail_pair = detail
    fields = {"theme": none, "captions": none, "speed": none, "voice": none}
    fields[detail_name] = detail_pair
    return UiClassification(
        route=answer(route),
        family=answer(family),
        memory_op=answer((memory_op, 0.9)),
        **{name: answer(pair) for name, pair in fields.items()},
    )


RECORDED = [
    (
        "Can you make it dark?",
        recorded(("conversation", 0.54), ("theme", 0.94), ("theme", ("dark", 0.88))),
        UiActionDecision(action=SetTheme(theme="dark")),
    ),
    (
        "It's too bright in here, can you fix that?",
        recorded(("ui_command", 0.76), ("theme", 0.90), ("theme", ("dark", 0.78))),
        None,
    ),
    (
        "Can you change to dark mode? (recorded live, route and detail weak)",
        recorded(("conversation", 0.57), ("theme", 0.91), ("theme", ("dark", 0.88))),
        UiActionDecision(action=SetTheme(theme="dark")),
    ),
    (
        "Can you bring back dark mode? (recorded live, below threshold)",
        recorded(("ui_command", 0.59), ("theme", 0.81), ("theme", ("dark", 0.56))),
        None,
    ),
    (
        "Can you bring back dark mode? (recorded after rewording)",
        recorded(("ui_command", 0.88), ("theme", 0.95), ("theme", ("dark", 0.90))),
        UiActionDecision(action=SetTheme(theme="dark")),
    ),
    (
        "You're talking too fast",
        recorded(("ui_command", 0.51), ("speed", 0.99), ("speed", ("slower", 0.91))),
        UiActionDecision(action=AdjustSpeakingSpeed(delta=-0.1)),
    ),
    (
        "Could you turn the captions off?",
        recorded(("ui_command", 0.67), ("captions", 0.99), ("captions", ("hide", 0.99))),
        UiActionDecision(action=SetCaptionsVisible(visible=False)),
    ),
    (
        "I want a woman to read this to me",
        recorded(("ui_command", 0.82), ("voice", 1.0), ("voice", ("female", 1.0))),
        UiVoiceGenderDecision(gender="female"),
    ),
    (
        "Show me what you're saying",
        recorded(("conversation", 0.87), ("none", 0.89), ("captions", ("none", 0.5))),
        None,
    ),
]


@pytest.mark.parametrize(("text", "result", "expected"), RECORDED, ids=[row[0] for row in RECORDED])
def test_recorded_live_answers(text, result, expected):
    assert evaluate_ui_classification(result) == expected


def test_results_are_immutable():
    with pytest.raises(Exception, match="frozen"):
        classification().route.probability = 0.1  # type: ignore[misc]


answers_strategy = st.builds(
    lambda route, family, theme, captions, speed, voice, ps: UiClassification(
        route=UiAnswer(choice=route, probability=ps[0]),
        family=UiAnswer(choice=family, probability=ps[1]),
        theme=UiAnswer(choice=theme, probability=ps[2]),
        captions=UiAnswer(choice=captions, probability=ps[3]),
        speed=UiAnswer(choice=speed, probability=ps[4]),
        voice=UiAnswer(choice=voice, probability=ps[5]),
        memory_op=UiAnswer(choice="none", probability=0.5),
    ),
    st.sampled_from(QUESTION_OPTIONS["route"]),
    st.sampled_from(QUESTION_OPTIONS["family"]),
    st.sampled_from(QUESTION_OPTIONS["theme"]),
    st.sampled_from(QUESTION_OPTIONS["captions"]),
    st.sampled_from(QUESTION_OPTIONS["speed"]),
    st.sampled_from(QUESTION_OPTIONS["voice"]),
    st.tuples(*[st.sampled_from([0.0, 0.5, 0.84, 0.85, 0.99, 1.0])] * 6),
)


@given(answers_strategy)
def test_action_only_when_family_and_detail_are_confident_and_agree_and_route_is_not_confident_chat(result):
    decision = evaluate_ui_classification(result)
    if not isinstance(decision, (UiActionDecision, UiVoiceGenderDecision)):
        return
    detail_name = result.family.choice
    detail = getattr(result, detail_name)
    assert not (result.route.choice == "conversation" and result.route.probability >= CONFIDENCE_THRESHOLD)
    assert result.route.choice != "multiple_commands"
    assert min(result.family.probability, detail.probability) >= CONFIDENCE_THRESHOLD
    assert detail.choice != "none"
    if isinstance(decision, UiActionDecision):
        assert detail_name in ("theme", "captions", "speed")
    else:
        assert detail_name == "voice" and detail.choice == decision.gender


def test_memory_op_is_parsed_exposed_and_never_acted_on():
    parsed = parse_ui_response(payload(route="conversation", memory_op="forget"))
    assert parsed is not None
    assert parsed.memory_op.choice == "forget"
    assert parsed.memory_op.probability == pytest.approx(0.96)
    assert evaluate_ui_classification(parsed) is None
    # A memory answer never alters a UI decision.
    assert evaluate_ui_classification(classification(family="theme", theme="dark", memory_op="remember")) == (
        UiActionDecision(action=SetTheme(theme="dark"))
    )
    bad = payload(memory_op="recall")
    bad["answers"]["memory_op"]["choice"] = "bogus"
    assert parse_ui_response(bad) is None
    missing = payload()
    del missing["answers"]["memory_op"]
    assert parse_ui_response(missing) is None


def test_client_returns_memory_op_from_the_single_request():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=payload(route="conversation", memory_op="remember"))

    result = asyncio.run(client_for(handler).classify(request_id="r", instruction="remember I like tea"))
    assert result.memory_op.choice == "remember"
    assert len(seen) == 1 and "memory_op" in seen[0]["questions"]


def test_request_shape_and_eligibility():
    body = build_ui_request("make it dark")
    assert body["model"] == JEV_MODEL
    assert body["state"] == {"current_instruction": "make it dark", "recent_context": [], "context_truncated": False}
    assert set(body["questions"]) == set(QUESTION_OPTIONS)
    for name, question in body["questions"].items():
        assert tuple(question["criteria"]) == QUESTION_OPTIONS[name]
    assert ui_text_is_eligible("please use dark mode")
    assert not ui_text_is_eligible("x" * 161)
    assert not ui_text_is_eligible(" ".join(["word"] * 26))
    assert not ui_text_is_eligible("   ")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(model="other"),
        lambda b: b.pop("answers"),
        lambda b: b["answers"].pop("speed"),
        lambda b: b["answers"]["route"].update(choice="bogus"),
        lambda b: b["answers"]["route"].update(type="text"),
        lambda b: b["answers"]["theme"].update(probabilities={"dark": 2.0}),
        lambda b: b["answers"]["theme"].update(probabilities={"zzz": 1.0}),
    ],
)
def test_malformed_payloads_are_rejected(mutate):
    body = payload()
    mutate(body)
    assert parse_ui_response(body) is None
    assert parse_ui_response("nope") is None


# Client ------------------------------------------------------------------------------------------------------------


async def allow(_request):
    return True


def client_for(handler, reserve=allow, key="test-key", **kwargs):
    return JevUiClient(key, reserve, transport=httpx.MockTransport(handler), **kwargs)


def test_client_valid_response_is_validated_and_request_is_authorized():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload(family="theme", theme="dark"))

    result = asyncio.run(client_for(handler).classify(request_id="r", instruction="go dark"))
    assert evaluate_ui_classification(result) == UiActionDecision(action=SetTheme(theme="dark"))
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(seen[0].content)["state"]["current_instruction"] == "go dark"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"model": JEV_MODEL, "answers": {}}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(500, json={}),
        httpx.Response(402, json={"error": "insufficient credit"}),
    ],
)
def test_client_failures_return_none_and_count(response):
    client = client_for(lambda _r: response)
    assert asyncio.run(client.classify(request_id="r", instruction="x")) is None
    assert client.circuit.consecutive_failures == 1


def test_client_timeout_returns_none():
    async def scenario():
        async def slow(_request):
            await asyncio.sleep(1)
            return httpx.Response(200, json=payload())

        client = JevUiClient("k", allow, transport=httpx.MockTransport(slow), deadline_seconds=0.05)
        return await client.classify(request_id="r", instruction="x")

    assert asyncio.run(scenario()) is None


def test_ui_deadline_is_its_own_constant_above_the_routing_deadline():
    from app.services.jev import DEFAULT_DEADLINE_SECONDS
    from app.services.jev_ui import UI_DEADLINE_SECONDS

    assert UI_DEADLINE_SECONDS == 1.2
    assert DEFAULT_DEADLINE_SECONDS == 0.75

    async def scenario():
        async def slowish(_request):
            await asyncio.sleep(0.9)
            return httpx.Response(200, json=payload())

        client = JevUiClient("k", allow, transport=httpx.MockTransport(slowish))
        return await client.classify(request_id="r", instruction="x")

    assert asyncio.run(scenario()) is not None


def test_question_wording_covers_polite_requests_and_excludes_non_commands():
    scope = build_ui_request("x")["questions"]["route"]["instructions"]
    for phrase in (
        "can you",
        "could you",
        "would you",
        "bring back",
        "go back to",
        "I love dark mode",
        "what are captions",
        "my brother talks too fast",
        "my essay",
        "don't switch",
    ):
        assert phrase in scope


def test_client_transport_error_returns_none():
    def boom(_request):
        raise httpx.ConnectError("down")

    assert asyncio.run(client_for(boom).classify(request_id="r", instruction="x")) is None


def test_circuit_opens_after_three_failures_and_stops_sending():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    client = client_for(handler)
    for _ in range(5):
        assert asyncio.run(client.classify(request_id="r", instruction="x")) is None
    assert len(calls) == 3
    assert client.circuit.is_open()


def test_missing_key_and_denied_or_failing_reservation_send_nothing():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=payload())

    async def deny(_request):
        return False

    async def explode(_request):
        raise RuntimeError("db down")

    assert asyncio.run(client_for(handler, key="").classify(request_id="r", instruction="x")) is None
    assert asyncio.run(client_for(handler, reserve=deny).classify(request_id="r", instruction="x")) is None
    assert asyncio.run(client_for(handler, reserve=explode).classify(request_id="r", instruction="x")) is None
    assert calls == []


def test_reserve_callback_is_mandatory():
    with pytest.raises(TypeError):
        JevUiClient("k", None)  # type: ignore[arg-type]


def test_jev_ledger_counts_requests_and_denies_at_limit(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jev.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        outcomes = [await reserve_jev_request(factory, limit=2) for _ in range(3)]
        await engine.dispose()
        return outcomes

    assert asyncio.run(scenario()) == [True, True, False]


# Integration through handle_finalized_utterance ----------------------------------------------------------------------


class FakeClassifier:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def classify(self, *, request_id, instruction):
        self.calls.append(instruction)
        if self.error:
            raise self.error
        return self.result


def speak(tmp_path, fake, text, name="jev", **settings):
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/{name}.db", _env_file=None, **settings),
        ui_classifier=fake,
    )
    with TestClient(app) as client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        response = client.post(
            "/v1/actions",
            json={
                "conversation_id": conversation,
                "request_id": "r1",
                "expected_revision": 0,
                "source": "transcript",
                "transcript": text,
            },
        )
        settings = client.get("/v1/settings").json()
    assert response.status_code == 200
    return response.json(), settings


def test_exact_phrase_wins_and_jev_is_not_called(tmp_path):
    fake = FakeClassifier(classification(family="theme", theme="light"))
    body, _ = speak(tmp_path, fake, "Switch to dark mode")
    assert fake.calls == []
    assert body["handled"] is True


def test_paraphrase_miss_calls_jev_once_and_applies_theme(tmp_path):
    fake = FakeClassifier(classification(family="theme", theme="light"))
    body, _ = speak(tmp_path, fake, "could you possibly brighten the interface to the light look")
    assert fake.calls == ["could you possibly brighten the interface to the light look"]
    assert body["result"]["kind"] == "executed"
    assert body["result"]["theme"] == "light"


def test_speed_and_captions_use_the_settings_path_once(tmp_path):
    fake = FakeClassifier(classification(family="speed", speed="faster"))
    body, settings = speak(tmp_path, fake, "talk a bit quicker for me please", "speed")
    assert len(fake.calls) == 1
    assert body["result"]["kind"] == "executed"
    assert body["result"]["settings"]["speaking_speed"] == pytest.approx(1.1)
    assert settings["speaking_speed"] == pytest.approx(1.1)
    fake = FakeClassifier(classification(family="captions", captions="hide"))
    body, settings = speak(tmp_path, fake, "I would prefer no subtitles on screen", "captions")
    assert body["result"]["kind"] == "executed"
    assert settings["captions_visible"] is False


def test_chat_falls_through(tmp_path):
    fake = FakeClassifier(classification(route="conversation"))
    body, _ = speak(tmp_path, fake, "I really love dark mode")
    assert len(fake.calls) == 1
    assert body == {"handled": False}


def test_compound_gets_one_change_at_a_time(tmp_path):
    fake = FakeClassifier(classification(route="multiple_commands"))
    body, settings = speak(tmp_path, fake, "dark mode and hide captions and talk faster")
    assert body["result"]["kind"] == "unsupported"
    assert "one change at a time" in body["result"]["spoken"]
    assert settings["speaking_speed"] == pytest.approx(1.0)


def test_gender_voice_without_approved_voice_is_honest_refusal(tmp_path):
    fake = FakeClassifier(classification(family="voice", voice="female"))
    # Only a male voice is approved, so the female request must not invent one.
    body, _ = speak(tmp_path, fake, "could I hear a woman speaking instead", default_voice_id="aura-2-zeus-en")
    assert body["result"]["detail"] == "VOICE_GENDER_NOT_FOUND"


@pytest.mark.parametrize(
    "fake",
    [
        FakeClassifier(None),
        FakeClassifier(error=RuntimeError("boom")),
        FakeClassifier(classification(family="theme", theme="dark", p=0.5)),
        FakeClassifier(classification(family="voice", voice="named")),
    ],
)
def test_failure_or_low_confidence_falls_through(tmp_path, fake):
    body, _ = speak(tmp_path, fake, "dark would be lovely right about now")
    assert body == {"handled": False}


@pytest.mark.parametrize("text", ["yes", "no.", "x" * 200, " ".join(["go"] * 30)])
def test_confirmations_and_long_text_skip_jev(tmp_path, text):
    fake = FakeClassifier(classification(family="theme", theme="dark"))
    speak(tmp_path, fake, text)
    assert fake.calls == []


def test_flag_and_credential_gating(tmp_path):
    def state(**settings):
        app = create_app(
            Settings(
                database_url=f"sqlite+aiosqlite:///{tmp_path}/g.db",
                _env_file=None,
                **settings,
            )
        )
        return app.state.ui_classifier

    assert state() is None
    assert state(jev_api_key="k", jev_commands_enabled=False) is None
    assert isinstance(state(jev_api_key="k"), JevUiClient)


def test_client_reuses_one_connection_and_closes_it():
    class Transport(httpx.AsyncBaseTransport):
        closed = 0

        async def handle_async_request(self, request):
            return httpx.Response(200, json=payload(family="theme", theme="dark"), request=request)

        async def aclose(self):
            Transport.closed += 1

    async def run():
        client = JevUiClient("k", allow, transport=Transport())
        for _ in range(3):
            assert await client.classify(request_id="r", instruction="go dark") is not None
        assert Transport.closed == 0
        await client.aclose()
        assert Transport.closed == 1

    asyncio.run(run())


def test_a_stale_connection_is_retried_once_and_is_not_a_provider_failure():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("closed")
        return httpx.Response(200, json=payload(family="theme", theme="dark"))

    client = client_for(handler)
    assert asyncio.run(client.classify(request_id="r", instruction="go dark")) is not None
    assert len(calls) == 2 and not client.circuit.is_open()


def test_a_closed_client_can_be_used_by_the_next_lifespan():
    client = client_for(lambda request: httpx.Response(200, json=payload(family="theme", theme="dark")))

    async def once():
        result = await client.classify(request_id="r", instruction="go dark")
        await client.aclose()
        return result

    assert asyncio.run(once()) is not None
    assert asyncio.run(once()) is not None
