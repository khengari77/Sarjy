import base64
import hashlib
import hmac
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.domain.context_operations import restore_diff, sign_proposal
from test_context_workflow import configured, seed


def test_no_signing_key_fails_closed_without_mutating(tmp_path):
    file=tmp_path/'unsigned.db'
    with TestClient(create_app(Settings(database_url=f'sqlite+aiosqlite:///{file}',_env_file=None))) as client:
        owner=client.post('/v1/demo-session').json()['id']; chat=client.post('/v1/conversations').json()['id']; seed(file,chat,[('trip','Trip to Paris')])
        base=f'/v1/conversations/{chat}/context'
        assert client.post(base+'/proposals',json={'query':'trip'}).status_code==503
        raw=json.dumps({'owner_id':owner,'conversation_id':chat,'revision':0,'excluded_ids':['trip'],'kept_ids':[]}).encode()
        body=base64.urlsafe_b64encode(raw).decode().rstrip('=')
        forged=body+'.'+hmac.new(b'',body.encode(),hashlib.sha256).hexdigest()
        assert client.post(base+'/confirm',json={'proposal':forged,'expected_revision':0,'idempotency_key':'forged'}).status_code==503
        assert client.get(base).json()['excluded_ids']==[]
    with pytest.raises(ValueError): sign_proposal({},'')


@pytest.mark.parametrize('same_key',[False,True])
def test_concurrent_confirmation_and_undo_have_typed_outcomes(tmp_path,same_key):
    file,settings=configured(tmp_path)
    with TestClient(create_app(settings),raise_server_exceptions=False) as client:
        client.post('/v1/demo-session');chat=client.post('/v1/conversations').json()['id'];seed(file,chat,[('trip','Trip to Paris')]);base=f'/v1/conversations/{chat}/context'
        preview=client.post(base+'/proposals',json={'query':'trip'}).json()
        def confirm(index):
            return client.post(base+'/confirm',json={'proposal':preview['proposal'],'expected_revision':0,'idempotency_key':'same' if same_key else str(index)})
        with ThreadPoolExecutor(2) as pool: replies=list(pool.map(confirm,range(2)))
        assert sorted(r.status_code for r in replies)==([200,200] if same_key else [200,409])
        operation=next(r.json()['operation_id'] for r in replies if r.status_code==200)
        assert client.get(base).json()['revision']==1
        def undo(index):
            return client.post(base+'/undo',json={'operation_id':operation,'expected_revision':1,'idempotency_key':'undo-same' if same_key else 'undo-'+str(index)})
        with ThreadPoolExecutor(2) as pool: replies=list(pool.map(undo,range(2)))
        assert sorted(r.status_code for r in replies)==([200,200] if same_key else [200,409])
        assert client.get(base).json()['revision']==2
        assert client.get(base).json()['excluded_ids']==[]


def test_redundant_preview_has_no_targets_and_effective_undo_diff_is_honest(tmp_path):
    file,settings=configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post('/v1/demo-session');chat=client.post('/v1/conversations').json()['id'];seed(file,chat,[('trip','Trip to Paris')]);base=f'/v1/conversations/{chat}/context'
        p=client.post(base+'/proposals',json={'query':'trip'}).json()
        assert client.post(base+'/confirm',json={'proposal':p['proposal'],'expected_revision':0,'idempotency_key':'one'}).status_code==200
        again=client.post(base+'/proposals',json={'query':'trip'}).json()
        assert again['diff']['removed']==[]
        assert client.post(base+'/confirm',json={'proposal':again['proposal'],'expected_revision':1,'idempotency_key':'two'}).status_code==409
        assert client.get(base).json()['revision']==1
    assert restore_diff(frozenset({'trip'}),frozenset({'trip'}),2,3)['added']==[]


def test_context_mutations_forbid_extra_fields_and_export_typed_responses(tmp_path):
    file,settings=configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post('/v1/demo-session');chat=client.post('/v1/conversations').json()['id'];seed(file,chat,[('trip','Trip to Paris')]);base=f'/v1/conversations/{chat}/context'
        assert client.post(base+'/proposals',json={'query':'trip','unexpected':'x'}).status_code==422
        assert client.post(base+'/confirm',json={'proposal':'x','expected_revision':0,'idempotency_key':'k','unexpected':1}).status_code==422
        assert client.post(base+'/undo',json={'operation_id':'x','expected_revision':0,'idempotency_key':'k','unexpected':1}).status_code==422
        spec=client.app.openapi()
        for suffix,method in (('', 'get'),('/operations','get'),('/proposals','post'),('/confirm','post'),('/undo','post')):
            path='/v1/conversations/{conversation_id}/context'+suffix
            schema=spec['paths'][path][method]['responses']['200']['content']['application/json']['schema']
            assert '$ref' in schema,(path,schema)
