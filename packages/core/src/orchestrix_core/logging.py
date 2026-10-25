"""Structured JSON logging (SPEC 7.1). Context (trace_id, run_id, task_id) rides on contextvars."""

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager

import structlog

CONTEXT_KEYS = ("trace_id", "run_id", "task_id")


def configure_logging(level: str = "INFO") -> None:
    """Route structlog and stdlib logging to one JSON stream on stdout."""
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


@contextmanager
def log_context(**values: str) -> Generator[None]:
    """Bind trace_id/run_id/task_id for everything logged inside the block."""
    unknown = set(values) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"unknown log context keys: {sorted(unknown)}")
    with structlog.contextvars.bound_contextvars(**values):
        yield


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
