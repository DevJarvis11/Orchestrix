"""Final memory score (SPEC 13.3, FR-53): rerank x (0.7 + 0.3 x importance x 0.5^(d/30))."""

from datetime import UTC, datetime

HALF_LIFE_DAYS = 30.0
TOP_K = 8
CANDIDATES = 20
DEDUPE_COSINE = 0.92


def decayed_importance(
    importance: float, last_used: datetime, now: datetime | None = None
) -> float:
    days = max(0.0, ((now or datetime.now(UTC)) - last_used).total_seconds() / 86400)
    return importance * 0.5 ** (days / HALF_LIFE_DAYS)


def final_score(
    rerank: float, importance: float, last_used: datetime, now: datetime | None = None
) -> float:
    return rerank * (0.7 + 0.3 * decayed_importance(importance, last_used, now))
