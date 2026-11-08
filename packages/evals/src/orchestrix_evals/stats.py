"""Statistics for eval reports (SPEC 20.2): bootstrap CIs, the regression rule, Cohen's kappa."""

import random
from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

BOOTSTRAP_SAMPLES = 2000
REGRESSION_POINTS = 5.0


@dataclass(frozen=True)
class Estimate:
    mean: float
    low: float
    high: float
    n: int


def bootstrap_ci(
    values: Sequence[float], *, samples: int = BOOTSTRAP_SAMPLES, level: float = 0.95, seed: int = 7
) -> Estimate:
    """Mean with a percentile bootstrap CI over cases (each value is one case's mean of its runs)."""
    n = len(values)
    if n == 0:
        return Estimate(0.0, 0.0, 0.0, 0)
    mean = sum(values) / n
    if n == 1:
        return Estimate(mean, mean, mean, 1)
    rng = random.Random(seed)  # noqa: S311 - resampling, not security
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(samples))
    tail = (1 - level) / 2
    low = means[int(tail * (samples - 1))]
    high = means[int((1 - tail) * (samples - 1))]
    return Estimate(mean, low, high, n)


def is_regression(new: Estimate, baseline: Estimate, *, points: float = REGRESSION_POINTS) -> bool:
    """SPEC 20.2: flagged only when the new mean is below the baseline's lower bound AND the drop
    exceeds `points` (estimates are in percentage points, 0-100)."""
    return new.mean < baseline.low and (baseline.mean - new.mean) > points


def cohens_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Agreement between two raters beyond chance. 1 = perfect, 0 = chance level."""
    if len(a) != len(b) or not a:
        raise ValueError("kappa needs two equally long, non-empty label lists")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def consensus(a: Sequence[Hashable], b: Sequence[Hashable]) -> list[Hashable | None]:
    """Human consensus: the label where both humans agree, None where they disagree."""
    return [x if x == y else None for x, y in zip(a, b, strict=True)]
