"""The Jev classification starts during turn/start and /command reuses it (issue #227)."""

import asyncio

import httpx
import pytest
from app.config import Settings
from app.main import create_app
from app.services import jev_ui
from app.services.command_classification import ClassificationStore, StoredClassifier, should_classify_early
from app.services.jev_ui import JevUiClient
from fastapi.testclient import TestClient
from test_finalized_commands import Catalog, _voice_session
from test_jev_ui_commands import FakeClassifier, classification, payload

PARAPHRASE = "could you possibly brighten the interface to the light look"


def worker(tmp_path, fake):
    file = tmp_path / "early.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}", agent_service_secret="test-service-secret", _env_file=None
    )
    return TestClient(create_app(settings, voice_catalog=Catalog(), ui_classifier=fake)), file


def start(client, headers, text, request="source-1"):
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request, "text": text})
    assert response.status_code == 200
    return response.json()["turn_id"]


def test_turn_start_starts_the_classification_and_command_reuses_it_once(tmp_path):
    fake = FakeClassifier(classification(family="theme", theme="light"))
    client, file = worker(tmp_path, fake)
    with client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        turn_id = start(client, headers, PARAPHRASE)
        # Before /command is asked for anything, Jev has already been called: that is the overlap.
        assert fake.calls == [PARAPHRASE]

        first = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id}).json()
        assert first["command"]["kind"] == "executed" and first["command"]["theme"] == "light"
        again = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id})
        assert again.status_code == 200
        assert fake.calls == [PARAPHRASE]


def test_a_conversational_turn_starts_one_early_call_and_command_answers_none(tmp_path):
    fake = FakeClassifier(classification(route="conversation"))
    client, file = worker(tmp_path, fake)
    with client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        turn_id = start(client, headers, "I really love dark mode")
        assert fake.calls == ["I really love dark mode"]
        body = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id}).json()
        assert body["command"] is None
        assert len(fake.calls) == 1


def test_wordle_mode_turns_start_no_early_call(tmp_path):
    fake = FakeClassifier(classification(route="conversation"))
    client, file = worker(tmp_path, fake)
    with client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        response = client.post(
            "/internal/agent/turn/start",
            headers=headers,
            json={"request_id": "g1", "text": "could you please try the word crane", "wordle_open": True},
        )
        assert response.status_code == 200
        assert fake.calls == []


@pytest.mark.parametrize("text", ["Hide captions.", "yes", "Forget my coffee order", "x" * 300])
def test_turns_that_never_reach_jev_do_not_start_a_call(tmp_path, text):
    fake = FakeClassifier(classification(route="conversation"))
    client, file = worker(tmp_path, fake)
    with client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        turn_id = start(client, headers, text)
        client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id})
        assert fake.calls == []


def test_a_replayed_turn_start_does_not_start_a_second_call(tmp_path):
    fake = FakeClassifier(classification(route="conversation"))
    client, file = worker(tmp_path, fake)
    with client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        start(client, headers, PARAPHRASE)
        start(client, headers, PARAPHRASE)
        assert fake.calls == [PARAPHRASE]


def test_without_a_stored_classification_command_classifies_for_itself(tmp_path):
    """A restart, an eviction or an older turn: today's behaviour, one call, same answer."""
    fake = FakeClassifier(classification(family="theme", theme="light"))
    client, file = worker(tmp_path, fake)
    with client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        turn_id = start(client, headers, PARAPHRASE)
        client.app.state.classification_store = ClassificationStore()  # forget what turn/start started
        body = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id}).json()
        assert body["command"]["theme"] == "light"
        assert fake.calls == [PARAPHRASE, PARAPHRASE]


# The store ----------------------------------------------------------------------------------------------------------


def test_the_prefilter_matches_what_command_answers_without_jev():
    assert should_classify_early(PARAPHRASE)
    for text in ["Hide captions.", "yes", "x" * 300, "Forget my coffee order", "undo", "bring back my memories"]:
        assert not should_classify_early(text), text


def test_store_keeps_one_task_per_key_and_text_and_replaces_a_changed_text():
    async def scenario():
        fake = FakeClassifier(classification(route="conversation"))
        store = ClassificationStore()
        store.start("k", PARAPHRASE, fake)
        first = store.take("k", PARAPHRASE)
        store.start("k", PARAPHRASE, fake)
        assert store.take("k", PARAPHRASE) is first
        assert store.take("k", "something else entirely different") is None
        other = PARAPHRASE + " please"
        store.start("k", other, fake)
        assert store.take("k", PARAPHRASE) is None and store.take("k", other) is not first
        await asyncio.sleep(0)
        return first.cancelled()

    assert asyncio.run(scenario()) is False


def test_store_evicts_the_oldest_beyond_capacity_and_forgets_expired_entries():
    async def scenario():
        now = [0.0]
        store = ClassificationStore(capacity=2, ttl_seconds=10, clock=lambda: now[0])

        class Slow:
            async def classify(self, *, request_id, instruction):
                await asyncio.sleep(30)

        for key in ("a", "b", "c"):
            store.start(key, PARAPHRASE, Slow())
        assert store.take("a", PARAPHRASE) is None and store.take("c", PARAPHRASE) is not None
        now[0] = 11.0
        assert store.take("b", PARAPHRASE) is None and store.take("c", PARAPHRASE) is None

    asyncio.run(scenario())


def test_a_cancelled_command_does_not_cancel_the_classification_a_retry_will_reuse():
    async def scenario():
        release = asyncio.Event()

        class Gate:
            async def classify(self, *, request_id, instruction):
                await release.wait()
                return classification(route="conversation")

        store = ClassificationStore()
        store.start("k", PARAPHRASE, Gate())
        waiter = asyncio.create_task(
            StoredClassifier(Gate(), store, "k").classify(request_id="r", instruction=PARAPHRASE)
        )
        await asyncio.sleep(0.01)
        waiter.cancel()
        await asyncio.sleep(0)
        task = store.take("k", PARAPHRASE)
        assert task is not None and not task.cancelled()
        release.set()
        assert (await task) is not None

    asyncio.run(scenario())


def test_a_failing_classifier_is_a_miss_not_an_error():
    async def scenario():
        store = ClassificationStore()
        store.start("k", PARAPHRASE, FakeClassifier(error=RuntimeError("boom")))
        task = store.take("k", PARAPHRASE)
        assert task is not None
        return await task

    assert asyncio.run(scenario()) is None


# The Jev client: the reservation is not provider time ---------------------------------------------------------------


def ok_handler(_request):
    return httpx.Response(200, json=payload())


def test_a_slow_reservation_does_not_consume_the_provider_deadline(monkeypatch):
    # Pin the reservation bound high so this tests "reservation time is not provider time", not the constant.
    monkeypatch.setattr(jev_ui, "RESERVATION_DEADLINE_SECONDS", 1.0)

    async def slow_reserve(_request):
        await asyncio.sleep(0.15)
        return True

    client = JevUiClient("k", slow_reserve, transport=httpx.MockTransport(ok_handler), deadline_seconds=0.1)
    assert asyncio.run(client.classify(request_id="r", instruction="x")) is not None


def test_a_reservation_past_its_own_bound_is_a_miss_that_never_trips_the_circuit(monkeypatch):
    monkeypatch.setattr(jev_ui, "RESERVATION_DEADLINE_SECONDS", 0.05)
    sent = []

    async def stuck(_request):
        await asyncio.sleep(1)
        return True

    def handler(request):
        sent.append(1)
        return httpx.Response(200, json=payload())

    client = JevUiClient("k", stuck, transport=httpx.MockTransport(handler))

    async def scenario():
        return [await client.classify(request_id="r", instruction="x") for _ in range(4)]

    assert asyncio.run(scenario()) == [None] * 4
    assert sent == [] and not client.circuit.is_open()


def test_evicting_an_entry_never_cancels_a_classification_a_command_is_awaiting():
    async def scenario():
        release = asyncio.Event()

        class Gate:
            async def classify(self, *, request_id, instruction):
                await release.wait()
                return classification(route="conversation")

        store = ClassificationStore(capacity=1)
        store.start("a", PARAPHRASE, Gate())
        waiter = asyncio.create_task(
            StoredClassifier(Gate(), store, "a").classify(request_id="r", instruction=PARAPHRASE)
        )
        await asyncio.sleep(0.01)
        store.start("b", PARAPHRASE + " now", Gate())  # evicts "a" while the waiter is inside it
        release.set()
        return await waiter

    assert asyncio.run(scenario()) is not None


def test_the_early_task_is_outside_the_starting_requests_statement_timer():
    """Its budget writes happen while turn/start is still being measured; they are not that request's cost."""
    from app.infrastructure.timing import StatementTiming, current_statement_timing

    seen = []

    class Probe:
        async def classify(self, *, request_id, instruction):
            seen.append(current_statement_timing.get())
            return None

    async def main():
        store = ClassificationStore()
        token = current_statement_timing.set(StatementTiming())
        try:
            store.start("c:r", PARAPHRASE, Probe())
            task = store.take("c:r", PARAPHRASE)
            await task
            assert current_statement_timing.get() is not None  # the caller keeps its own timer
        finally:
            current_statement_timing.reset(token)

    asyncio.run(main())
    assert seen == [None]
