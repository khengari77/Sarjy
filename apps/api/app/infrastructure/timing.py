"""Request-scoped database timing for internal worker routes."""

import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.engine import Engine


def timing_logger(name: str) -> logging.Logger:
    """A dedicated INFO logger: uvicorn leaves app loggers at WARNING and prints only the message."""
    configured = logging.getLogger(name)
    if not configured.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(f"%(levelname)s {name} %(message)s"))
        configured.addHandler(handler)
        configured.propagate = False
        configured.setLevel(logging.INFO)
    return configured


logger = timing_logger("sarjy.statements")
# A statement this slow is a network or pooler stall: Postgres itself executes ours in about a millisecond.
SLOW_STATEMENT_MS = 250.0


@dataclass
class StatementTiming:
    ms: float = 0.0
    count: int = 0


current_statement_timing: ContextVar[StatementTiming | None] = ContextVar("current_statement_timing", default=None)


def install_statement_timing(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
        if current_statement_timing.get() is not None:
            context._sarjy_timing_started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(connection, cursor, statement, parameters, context, executemany):
        timing = current_statement_timing.get()
        started = getattr(context, "_sarjy_timing_started", None)
        if timing is None or started is None:
            return
        elapsed = (time.perf_counter() - started) * 1000
        timing.ms += elapsed
        timing.count += 1
        # SARJY_LOG_STATEMENTS=1 logs every statement; slow ones are always logged. SQL text only, no parameters.
        slow = elapsed >= SLOW_STATEMENT_MS
        if slow or os.environ.get("SARJY_LOG_STATEMENTS") == "1":
            logger.log(
                logging.WARNING if slow else logging.INFO,
                "statement %d: %.1f ms %s",
                timing.count,
                elapsed,
                " ".join(statement.split())[:120],
            )
