import os

import pytest
from config import read_worker_environment, require_values


def test_cli_imported_job_gets_file_credentials_without_mutating_environment(tmp_path):
    file=tmp_path/'.env'
    file.write_text('AGENT_SERVICE_SECRET=local-secret\nAPI_INTERNAL_URL=http://localhost:8000\nOPENAI_API_KEY=local-key\n')
    before=dict(os.environ)
    result=read_worker_environment(file,{})
    assert require_values(result,'AGENT_SERVICE_SECRET','OPENAI_API_KEY') == {'AGENT_SERVICE_SECRET':'local-secret','OPENAI_API_KEY':'local-key'}
    assert dict(os.environ) == before


def test_injected_environment_takes_precedence_and_missing_values_fail_closed(tmp_path):
    file=tmp_path/'.env'
    file.write_text('AGENT_SERVICE_SECRET=file-secret\n')
    assert read_worker_environment(file,{'AGENT_SERVICE_SECRET':'injected-secret'})['AGENT_SERVICE_SECRET'] == 'injected-secret'
    with pytest.raises(ValueError,match='AGENT_SERVICE_SECRET'):
        require_values(read_worker_environment(file,{'AGENT_SERVICE_SECRET':''}),'AGENT_SERVICE_SECRET')
