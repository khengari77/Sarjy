"""Run Alembic in-process so migration tests do not boot an interpreter per command."""

import asyncio
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT_SECONDS = 120


def _config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(REPO_ROOT / "apps" / "api"))
    return config


@contextmanager
def _database_url(url: str):
    # migrations/env.py reads the URL through Settings, which prefers the environment over .env.
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _execute(url: str, action: str, revision: str | None) -> None:
    runner = getattr(command, action)
    with _database_url(url):
        if revision is None:
            runner(_config())
        else:
            runner(_config(), revision)


def run_alembic(url: str, action: str, revision: str | None = None) -> None:
    """Run one Alembic command (upgrade, downgrade, check...) against ``url``.

    env.py calls ``asyncio.run``, so a command issued from inside a running loop is
    executed on a worker thread instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _execute(url, action, revision)
        return
    failure: list[BaseException] = []

    def target() -> None:
        try:
            _execute(url, action, revision)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller; SystemExit must not look like success
            failure.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    # The old subprocess helper gave each command 120 s; a wedged migration must fail, not hang the session.
    thread.join(COMMAND_TIMEOUT_SECONDS)
    if thread.is_alive():
        raise TimeoutError(f"alembic {action} did not finish within {COMMAND_TIMEOUT_SECONDS} s")
    if failure:
        raise failure[0]
