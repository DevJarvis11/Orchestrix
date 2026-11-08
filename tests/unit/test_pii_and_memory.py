"""PII redaction (FR-93) and memory rules (FR-53, FR-56; SPEC 13.2-13.3)."""

from datetime import UTC, datetime, timedelta

import pytest

from orchestrix_agents.context import RunDigest
from orchestrix_core.pii import Redactor
from orchestrix_core.schemas import TaskInput
from orchestrix_memory.repo import memory_id
from orchestrix_memory.scoring import decayed_importance, final_score
from orchestrix_memory.service import MemoryCandidate, namespace_for, query_text, validate

NOW = datetime(2026, 11, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "placeholder"),
    [
        ("mail bob.smith@example.com today", "[EMAIL]"),
        ("call +44 20 7946 0958 now", "[PHONE]"),
        ("card 4111 1111 1111 1111", "[CARD]"),
        ("ssn 123-45-6789", "[GOV_ID]"),
    ],
)
def test_fr93_patterns_are_replaced_with_typed_placeholders(text: str, placeholder: str) -> None:
    assert placeholder in Redactor().text(text)


def test_fr93_patterns_are_configurable_and_nested_data_is_redacted() -> None:
    redactor = Redactor({"PHONE": "", "TICKET": r"TCK-\d+"})
    assert redactor.text("TCK-42 at +1 415 555 0100") == "[TICKET] at +1 415 555 0100"
    assert Redactor().any({"a": ["x@y.io", 3]}) == {"a": ["[EMAIL]", 3]}


def test_fr53_importance_halves_every_30_days() -> None:
    assert decayed_importance(0.8, NOW, NOW) == pytest.approx(0.8)
    assert decayed_importance(0.8, NOW - timedelta(days=30), NOW) == pytest.approx(0.4)
    fresh = final_score(1.0, 1.0, NOW, NOW)
    stale = final_score(1.0, 1.0, NOW - timedelta(days=90), NOW)
    assert fresh == pytest.approx(1.0)
    assert 0.7 < stale < fresh


def digest() -> RunDigest:
    task = TaskInput(id="t", title="Vector DBs", description="I prefer short reports with tables.")
    return RunDigest(
        task=task,
        plan=None,
        outcome="completed",
        reviews={},
        errors=[],
        escalations=[],
        sources=["https://qdrant.tech/benchmarks"],
        user_words=[task.description],
    )


def test_fr56_fact_needs_a_cited_source() -> None:
    ok = MemoryCandidate(
        kind="fact", text="x", importance=0.5, source_ref="https://qdrant.tech/benchmarks"
    )
    bad = MemoryCandidate(kind="fact", text="x", importance=0.5, source_ref="https://made.up")
    assert validate(ok, digest()) is None
    assert validate(bad, digest()) is not None
    assert validate(MemoryCandidate(kind="fact", text="x", importance=0.5), digest()) is not None


def test_fr56_preference_must_quote_the_users_own_words() -> None:
    ok = MemoryCandidate(
        kind="preference", text="Likes tables", importance=0.5, quote="short reports with tables"
    )
    bad = MemoryCandidate(
        kind="preference", text="Likes charts", importance=0.5, quote="loves charts"
    )
    assert validate(ok, digest()) is None
    assert validate(bad, digest()) is not None


def test_namespaces_and_deterministic_ids() -> None:
    assert namespace_for("preference", "u1", "t1") == "user:u1"
    assert namespace_for("lesson", "u1", "t1") == "team:t1"
    assert memory_id("r", "lesson", "A  b") == memory_id("r", "lesson", "a b")  # re-runs collide
    assert "Vector DBs" in query_text(digest().task)
