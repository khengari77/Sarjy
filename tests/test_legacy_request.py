from copy import deepcopy
import pytest
from app.domain.context import compile_turn_context, ContextCandidate
from snapshot_request import snapshot_request, SnapshotCompatibilityError


def legacy():
    return compile_turn_context(revision=3, instructions='Frozen rules',
        current_turn=ContextCandidate(id='turn', role='user', text='Original question', origin='history'),
        excluded_ids={'excluded'}, pipeline={'response_tier': 3}).to_snapshot()


def test_legacy_adaptation_preserves_frozen_request_and_unknown_metadata():
    snapshot = legacy()
    before = deepcopy(snapshot)
    result = snapshot_request(snapshot, 'turn')
    assert snapshot == before
    assert [item.model_dump() for item in result.messages] == snapshot['messages']
    assert result.selection.revision == 3 and result.response_policy.max_output_tokens == 256
    assert result.settings is None and result.settings_revision is None
    assert result.memories == ()
    assert result.selection.excluded_ids == {'excluded'}


@pytest.mark.parametrize('corruption', ['messages', 'revision', 'turn'])
def test_legacy_corruption_is_typed_failure(corruption):
    snapshot = legacy()
    turn = 'turn'
    if corruption == 'messages': snapshot['messages'][0]['content'] = 'Modified rules'
    elif corruption == 'revision': snapshot['revision'] += 1
    else: turn = 'other-turn'
    with pytest.raises(SnapshotCompatibilityError, match='SNAPSHOT_INCOMPATIBLE'):
        snapshot_request(snapshot, turn)


def test_api_replay_adapts_legacy_snapshot_without_rewriting_history(tmp_path):
    import json, sqlite3
    from fastapi.testclient import TestClient
    from test_context_workflow import configured, create_app
    from test_context_assembly import start_turn
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner = client.post('/v1/demo-session').json()['id']
        chat = client.post('/v1/conversations').json()['id']
        headers, first = start_turn(client, chat, owner, file, memories=())
        old = deepcopy(first['context'])
        old.pop('model_request')
        with sqlite3.connect(file) as db:
            db.execute('UPDATE turns SET snapshot=? WHERE id=?', (json.dumps(old), first['turn_id']))
            db.commit()
        replay = client.post('/internal/agent/turn/start', headers=headers,
            json={'request_id': 'request-1', 'text': 'What is the weather?'})
        assert replay.status_code == 200, replay.text
        assert replay.json()['context'] == old
        adapted = snapshot_request(replay.json()['context'], replay.json()['turn_id'])
        assert [message.model_dump() for message in adapted.messages] == old['messages']
        with sqlite3.connect(file) as db:
            assert json.loads(db.execute('SELECT snapshot FROM turns WHERE id=?', (first['turn_id'],)).fetchone()[0]) == old


@pytest.mark.parametrize('field,value', [('manifest', None), ('pipeline_config', None),
    ('included_record_ids', ['wrong', 'identifiers']), ('excluded_record_ids', [])])
def test_malformed_legacy_metadata_is_typed_failure(field, value):
    snapshot = legacy()
    snapshot[field] = value
    with pytest.raises(SnapshotCompatibilityError):
        snapshot_request(snapshot, 'turn')


def test_malformed_new_snapshot_is_also_typed_failure():
    old = legacy()
    request = snapshot_request(old, 'turn')
    with pytest.raises(SnapshotCompatibilityError):
        snapshot_request({'model_request': request.model_dump(mode='json')}, 'turn')
