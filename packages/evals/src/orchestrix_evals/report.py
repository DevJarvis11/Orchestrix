"""Markdown reports (SPEC 20.2): scores with bootstrap 95% CIs, per-category breakdown, the 5 worst
cases with trace links, regressions against a baseline, and side-by-side model configurations (A4)."""

from pathlib import Path

from orchestrix_core.config import get_settings
from orchestrix_core.logging import get_logger
from orchestrix_evals.stats import Estimate, is_regression
from orchestrix_evals.suites import MetricEstimate, SuiteResult

log = get_logger(__name__)

# SPEC section 1 targets, where a suite measures one.
TARGETS: dict[str, tuple[str, float]] = {
    "plan_validity": (">=", 98.0),
    "task_success": (">=", 80.0),
    "catch_rate": (">=", 85.0),
    "escalation_precision": (">=", 75.0),
    "unsafe_executions_total": ("==", 0.0),
}


def fmt(m: MetricEstimate) -> str:
    if m.unit == "%":
        return f"{m.mean:.1f}% [{m.low:.1f}, {m.high:.1f}]"
    if m.unit == "usd":
        return f"${m.mean:.4f} [{m.low:.4f}, {m.high:.4f}]"
    return f"{m.mean:.2f} [{m.low:.2f}, {m.high:.2f}]"


def target_status(name: str, m: MetricEstimate) -> str:
    if name not in TARGETS:
        return ""
    op, value = TARGETS[name]
    met = m.mean >= value if op == ">=" else m.mean == value
    return f"{'met' if met else 'NOT met'} ({op} {value:g})"


def _est(m: MetricEstimate) -> Estimate:
    return Estimate(m.mean, m.low, m.high, m.n)


def markdown(result: SuiteResult, baseline: SuiteResult | None = None) -> str:
    lines = [
        f"# Eval report: {result.suite} · {result.label}",
        "",
        f"- Models: `{result.models or 'stack defaults'}`",
        f"- Judge (reviewer role): `{result.judge_model}`",
        f"- Runs per case: {result.runs_per_case}; cases: {len(result.cases)}",
        f"- Started {result.started_at:%Y-%m-%d %H:%M} UTC, took "
        f"{(result.finished_at - result.started_at).total_seconds() / 60:.1f} min",
        "- Means with bootstrap 95% confidence intervals over cases.",
        "",
        "## Metrics",
        "",
        "| Metric | Value [95% CI] | n | SPEC 1 target | vs baseline |",
        "|---|---|---|---|---|",
    ]
    for name, m in result.metrics.items():
        versus = ""
        if baseline is not None and name in baseline.metrics:
            b = baseline.metrics[name]
            delta = m.mean - b.mean
            flagged = m.unit == "%" and is_regression(_est(m), _est(b))
            versus = f"{delta:+.1f}{' **REGRESSION**' if flagged else ''}"
        marker = " (headline)" if name == result.headline else ""
        lines.append(f"| {name}{marker} | {fmt(m)} | {m.n} | {target_status(name, m)} | {versus} |")
    if result.by_category:
        lines += ["", "## By category", "", "| Category | Value [95% CI] | n |", "|---|---|---|"]
        lines += [f"| {cat} | {fmt(m)} | {m.n} |" for cat, m in result.by_category.items()]
    worst = sorted(result.cases, key=lambda c: c.score)[:5]
    lines += ["", "## Five worst cases", ""]
    for case in worst:
        link = next((r.trace_url for r in case.runs if r.trace_url), None)
        note = next((n for r in case.runs for n in r.notes), "")
        lines.append(
            f"- **{case.id}** ({case.category}): score {case.score * 100:.0f}%"
            + (f" · [trace]({link})" if link else "")
            + (f" · {note[:240]}" if note else "")
        )
    lines.append("")
    return "\n".join(lines)


def comparison(results: list[SuiteResult]) -> str:
    """A4: model configurations side by side, per metric."""
    names: list[str] = []
    for r in results:
        names += [n for n in r.metrics if n not in names]
    header = "| Metric | " + " | ".join(f"{r.label} ({r.suite})" for r in results) + " |"
    lines = [
        "# Model configurations side by side",
        "",
        "Means with bootstrap 95% CIs. Reviewer and specialist sharing a model weakens FR-32; the "
        "judge model is listed per column.",
        "",
        header,
        "|---|" + "---|" * len(results),
        "| judge | " + " | ".join(f"`{r.judge_model}`" for r in results) + " |",
    ]
    for name in names:
        cells = [fmt(r.metrics[name]) if name in r.metrics else "n/a" for r in results]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write(result: SuiteResult, out: Path, baseline: SuiteResult | None = None) -> tuple[Path, Path]:
    out.parent.mkdir(parents=True, exist_ok=True)
    md = out.with_suffix(".md")
    js = out.with_suffix(".json")
    md.write_text(markdown(result, baseline))
    js.write_text(result.model_dump_json(indent=2))
    return md, js


def load(path: Path) -> SuiteResult:
    return SuiteResult.model_validate_json(path.read_text())


def push_langfuse(result: SuiteResult) -> bool:
    """Best-effort: one dataset per suite, items linked to each case's first run trace, and a
    score per run on its trace. Returns False when Langfuse is not configured or unreachable."""
    settings = get_settings()
    if not (
        settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key
    ):
        return False
    try:
        from orchestrix_core.telemetry import init_tracing, run_trace_id

        init_tracing("orchestrix-evals", settings)
        from langfuse import get_client

        client = get_client()
        dataset = f"orchestrix-{result.suite}"
        try:
            client.create_dataset(name=dataset, description=f"Orchestrix eval suite {result.suite}")
        except Exception:  # already exists
            log.info("langfuse dataset exists", dataset=dataset)
        for case in result.cases:
            first = next((r for r in case.runs if r.run_id), None)
            client.create_dataset_item(
                dataset_name=dataset,
                id=f"{result.suite}-{case.id}",
                input={"case": case.id, "category": case.category},
                metadata={"models": result.label},
                source_trace_id=run_trace_id(first.run_id) if first and first.run_id else None,
            )
            for run in case.runs:
                if run.run_id:
                    client.create_score(
                        name=f"{result.suite}.{result.headline}",
                        value=run.score,
                        trace_id=run_trace_id(run.run_id),
                        comment=f"{result.label} run {run.run}",
                    )
        client.flush()
        return True
    except Exception as exc:
        log.warning("langfuse push failed", error=str(exc))
        return False
