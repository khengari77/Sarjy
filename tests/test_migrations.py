import os
import subprocess

def test_initial_migration_matches_models(tmp_path):
    env = os.environ | {'DATABASE_URL':f'sqlite+aiosqlite:///{tmp_path}/migration.db'}
    for command in [['upgrade','head'],['check']]:
        result = subprocess.run(['.venv/bin/alembic',*command],env=env,capture_output=True,text=True,timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr


def test_cleanup_migration_preserves_existing_sessions_and_ledger(tmp_path):
    import sqlite3
    path = tmp_path / 'cleanup.db'
    env = os.environ | {'DATABASE_URL':f'sqlite+aiosqlite:///{path}'}
    def migrate(*args):
        result = subprocess.run(['.venv/bin/alembic',*args],env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    migrate('upgrade','0003')
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO identities VALUES ('owner','hash','2030-01-01')")
        db.execute("INSERT INTO conversations VALUES ('chat','owner','Test',0,'2026-11-11')")
        db.execute("INSERT INTO voice_sessions VALUES ('session','chat','owner','room','ended','2026-11-11','{}')")
        db.execute("INSERT INTO development_budgets VALUES ('2026-11-11','openai',20000)")
        db.commit()
    migrate('upgrade','head')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT room_cleanup_completed_at FROM voice_sessions').fetchone() == (None,)
        assert db.execute('SELECT reserved_units FROM development_budgets').fetchone() == (20000,)
    migrate('check')
    migrate('downgrade','0003')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id,status FROM voice_sessions').fetchone() == ('session','ended')
