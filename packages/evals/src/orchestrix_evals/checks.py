"""Deterministic checks (SPEC 20.2): on deliverables, and trajectory checks from run traces."""

import csv
import io
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from orchestrix_agents.specialists import SPECIALIST_TOOLS
from orchestrix_evals.jsonutil import obj
from orchestrix_evals.models import ATTACHMENTS, Check

URL_RE = re.compile(r"https?://[^\s)\]>\"'`]+")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


@dataclass(frozen=True)
class CheckResult:
    kind: str
    passed: bool
    detail: str


def urls(text: str) -> list[str]:
    return [u.rstrip(".,;:") for u in URL_RE.findall(text)]


def _norm_url(url: str) -> str:
    return (
        url.lower()
        .rstrip("/")
        .removeprefix("https://")
        .removeprefix("http://")
        .removeprefix("www.")
    )


def csv_aggregate(name: str, column: str, agg: str, where: dict[str, str]) -> float:
    rows = list(csv.DictReader(io.StringIO((ATTACHMENTS / name).read_text())))
    rows = [r for r in rows if all(r.get(k) == v for k, v in where.items())]
    values = [float(r[column]) for r in rows if r.get(column) not in (None, "")]
    if agg == "count":
        return float(len(rows))
    if not values:
        raise ValueError(f"no values for {column} in {name} with {where}")
    return {
        "mean": statistics.fmean,
        "median": statistics.median,
        "max": max,
        "min": min,
        "sum": sum,
    }[agg](values)


def _number_present(text: str, value: float, decimals: list[int]) -> bool:
    plain = text.replace(",", "")
    for d in decimals:
        rendered = f"{value:.{d}f}"
        if re.search(rf"(?<![\d.]){re.escape(rendered)}(?![\d])", plain):
            return True
    return False


def run_check(check: Check, text: str, sources: set[str]) -> CheckResult:
    lower = text.lower()
    match check.kind:
        case "sections":
            headings = [h.lower() for h in HEADING_RE.findall(text)]
            missing = [v for v in check.values if not any(v.lower() in h for h in headings)]
            return CheckResult(
                check.kind, not missing, f"missing headings: {missing}" if missing else "ok"
            )
        case "contains_all":
            missing = [v for v in check.values if v.lower() not in lower]
            return CheckResult(check.kind, not missing, f"missing: {missing}" if missing else "ok")
        case "contains_any":
            hit = [v for v in check.values if v.lower() in lower]
            return CheckResult(
                check.kind, bool(hit), f"found {hit}" if hit else f"none of {check.values}"
            )
        case "min_citations":
            found = len(set(urls(text)))
            return CheckResult(
                check.kind, found >= check.n, f"{found} distinct URLs (need {check.n})"
            )
        case "citations_resolve":
            cited = {_norm_url(u) for u in urls(text)}
            known = {_norm_url(s) for s in sources}
            unknown = sorted(c for c in cited if c not in known)
            ok = bool(cited) and not unknown
            return CheckResult(
                check.kind, ok, f"unresolved: {unknown[:5]}" if unknown else f"{len(cited)} cited"
            )
        case "csv_number":
            if not (check.csv and check.column):
                return CheckResult(check.kind, False, "check needs csv and column")
            value = csv_aggregate(check.csv, check.column, check.agg, check.where)
            ok = _number_present(text, value, check.decimals)
            return CheckResult(
                check.kind, ok, f"{check.agg}({check.column}{check.where or ''}) = {value:.3f}"
            )
        case "code_block":
            ok = "```" in text
            return CheckResult(
                check.kind, ok, "fenced code present" if ok else "no fenced code block"
            )
        case "words_between":
            words = len(text.split())
            ok = check.min <= words <= check.max
            return CheckResult(check.kind, ok, f"{words} words (want {check.min}-{check.max})")


def run_checks(checks: list[Check], text: str, sources: set[str]) -> list[CheckResult]:
    return [run_check(c, text, sources) for c in checks]


# --- trajectory checks (SPEC 20.2 last row) ------------------------------------------------


@dataclass
class Trajectory:
    wrong_tool_calls: int = 0
    repeated_tool_calls: int = 0  # identical tool + args within one subtask, beyond the first
    loops: int = 0  # subtasks that cycled (>= 3 identical calls, or attempts+revisions >= 3)
    steps_per_subtask: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def mean_steps(self) -> float:
        steps = list(self.steps_per_subtask.values())
        return statistics.fmean(steps) if steps else 0.0


def trajectory(trace: dict[str, Any]) -> Trajectory:
    """From GET /v1/runs/{id}/trace: subtask nodes are labelled "<key> · <specialist>" and hold
    their llm_call and tool_call children."""
    out = Trajectory()
    for node in trace.get("root", {}).get("children", []):
        if node.get("kind") != "subtask":
            continue
        key, _, specialist = str(node.get("label", "")).partition(" · ")
        allowed = SPECIALIST_TOOLS.get(specialist, frozenset())  # type: ignore[call-overload]
        children: list[dict[str, Any]] = list(node.get("children", []))
        tools = [c for c in children if c.get("kind") == "tool_call"]
        steps = sum(1 for c in children if c.get("kind") in ("tool_call", "llm_call"))
        out.steps_per_subtask[key] = steps
        signatures: Counter[str] = Counter()
        for t in tools:
            name = str(t.get("label", ""))
            if allowed and name not in allowed:
                out.wrong_tool_calls += 1
            args = obj(t.get("payload")).get("args", {})
            signatures[f"{name}|{json.dumps(args, sort_keys=True)}"] += 1
        out.repeated_tool_calls += sum(n - 1 for n in signatures.values() if n > 1)
        payload = obj(node.get("payload"))
        cycled = int(payload.get("attempts", 0)) + int(payload.get("revisions", 0)) >= 3
        if cycled or any(n >= 3 for n in signatures.values()):
            out.loops += 1
    return out
