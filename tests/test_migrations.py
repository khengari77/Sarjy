import os
import subprocess

def test_initial_migration_matches_models(tmp_path):
    env = os.environ | {'DATABASE_URL':f'sqlite+aiosqlite:///{tmp_path}/migration.db'}
    for command in [['upgrade','head'],['check']]:
        result = subprocess.run(['.venv/bin/alembic',*command],env=env,capture_output=True,text=True,timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
