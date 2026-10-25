"""structlog JSON output carries trace_id/run_id/task_id from contextvars (SPEC 7.1)."""

import json

import pytest

from orchestrix_core.logging import configure_logging, get_logger, log_context


def test_json_lines_carry_bound_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    with log_context(run_id="r1", task_id="t1", trace_id="abc"):
        get_logger("test").info("step done", node="plan")
    get_logger("test").info("outside")

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    inside, outside = lines[-2], lines[-1]
    assert inside["event"] == "step done"
    assert (inside["run_id"], inside["task_id"], inside["trace_id"]) == ("r1", "t1", "abc")
    assert inside["node"] == "plan"
    assert inside["level"] == "info"
    assert "run_id" not in outside


def test_stdlib_loggers_render_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    import logging

    configure_logging()
    with log_context(run_id="r2"):
        logging.getLogger("uvicorn").warning("hello")
    line = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert line["event"] == "hello"
    assert line["run_id"] == "r2"


def test_unknown_context_key_rejected() -> None:
    with pytest.raises(ValueError, match="unknown log context"), log_context(user="x"):
        pass
