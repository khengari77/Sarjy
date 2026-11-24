"""Request-scoped database timing for internal worker routes."""

import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger("sarjy.statements")
# A statement this slow is a network or pooler stall: Postgres itself executes ours in about a millisecond.
SLOW_STATEMENT_MS = 250.0
if not logger.handlers:  # the root logger only prints warnings, so INFO lines would vanish without this
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s sarjy.statements %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False
    logger.setLevel(logging.INFO if os.environ.get("SARJY_LOG_STATEMENTS") == "1" else logging.WARNING)


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
        level = logging.WARNING if elapsed >= SLOW_STATEMENT_MS else logging.INFO
        logger.log(level, "statement %d: %.1f ms %s", timing.count, elapsed, " ".join(statement.split())[:120])
