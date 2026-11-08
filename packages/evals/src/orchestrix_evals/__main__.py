"""CLI: python -m orchestrix_evals {run,compare,export-labels,calibrate} (SPEC 20.2).

run --suite smoke|full|golden|defects|escalation|injection|memory [--models M]... [--runs 3]
compare reports/a.json reports/b.json --out reports/compare.md
export-labels reports/smoke.json --out reports/calibration/
calibrate --outputs reports/calibration/outputs.jsonl --human-a .. --human-b .. [--judge-labels ..]
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from orchestrix_core.logging import configure_logging
from orchestrix_evals import calibrate, report
from orchestrix_evals.client import DEFAULT_API
from orchestrix_evals.judge import Judge, models_label, parse_models, provider
from orchestrix_evals.suites import Harness, SuiteName, SuiteResult

SUITES: tuple[SuiteName, ...] = (
    "smoke",
    "full",
    "golden",
    "defects",
    "escalation",
    "injection",
    "memory",
)


def _judge(spec: str | None) -> Judge:
    return Judge(provider(parse_models(spec)))


async def cmd_run(a: argparse.Namespace) -> int:
    configs = a.models or ["default"]
    baseline = report.load(Path(a.baseline)) if a.baseline else None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    results: list[SuiteResult] = []
    regressed = False
    for spec in configs:
        models = parse_models(spec)
        harness = Harness(
            api_url=a.api,
            web_url=a.web,
            models=models,
            judge=_judge(a.judge_models),
            runs=a.runs,
            concurrency=a.concurrency,
            limit=a.limit,
            timeout_s=a.timeout,
        )
        result = await harness.run(a.suite)
        results.append(result)
        base = (
            Path(a.out)
            if a.out and len(configs) == 1
            else Path(a.out or "reports")
            / f"{stamp}-{a.suite}-{models_label(models).replace('/', '_')}"
        )
        md, js = report.write(result, base, baseline)
        print(f"wrote {md} and {js}")
        if a.push_langfuse and report.push_langfuse(result):
            print("pushed to langfuse")
        if baseline is not None and "REGRESSION" in md.read_text():
            regressed = True
    if len(results) > 1:
        cmp = Path(a.out or "reports") / f"{stamp}-{a.suite}-compare.md"
        cmp.write_text(report.comparison(results))
        print(f"wrote {cmp}")
    return 1 if regressed else 0


def cmd_compare(a: argparse.Namespace) -> int:
    results = [report.load(Path(p)) for p in a.results]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.comparison(results))
    print(f"wrote {out}")
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    outputs = calibrate.export_outputs(report.load(Path(a.results)), Path(a.out), n=a.n)
    print(f"wrote {outputs}; give human_a.jsonl and human_b.jsonl to two labellers")
    return 0


async def cmd_calibrate(a: argparse.Namespace) -> int:
    judge_file = (
        Path(a.judge_labels) if a.judge_labels else Path(a.outputs).with_name("judge.jsonl")
    )
    if not a.judge_labels:
        await calibrate.judge_outputs(_judge(a.judge_models), Path(a.outputs), judge_file)
        print(f"wrote judge labels to {judge_file}")
    result = calibrate.kappa(judge_file, Path(a.human_a), Path(a.human_b))
    print(result.model_dump_json(indent=2))
    return 0 if result.trusted else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrix_evals",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a suite against the running stack")
    run.add_argument("--suite", choices=SUITES, required=True)
    run.add_argument(
        "--models",
        action="append",
        help="model for every role, or a role->model JSON; repeat to compare (A4)",
    )
    run.add_argument(
        "--judge-models",
        default=os.environ.get("ORCHESTRIX_EVAL_JUDGE_MODELS"),
        help="judge (reviewer role) model; prefer a strong model",
    )
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--limit", type=int, help="only the first N cases")
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--timeout", type=float, default=1800, help="seconds per run")
    run.add_argument("--baseline", help="a previous result .json; flags regressions (SPEC 20.2)")
    run.add_argument("--out", help="output path without suffix (.md and .json are written)")
    run.add_argument("--api", default=os.environ.get("ORCHESTRIX_EVAL_API_URL", DEFAULT_API))
    run.add_argument(
        "--web", default=os.environ.get("ORCHESTRIX_EVAL_WEB_URL", "http://localhost:5173")
    )
    run.add_argument("--push-langfuse", action="store_true")
    cmp = sub.add_parser("compare", help="side-by-side report of result .json files (A4)")
    cmp.add_argument("results", nargs="+")
    cmp.add_argument("--out", default="reports/compare.md")
    exp = sub.add_parser("export-labels", help="sample golden outputs for human labelling")
    exp.add_argument("results")
    exp.add_argument("--out", default="reports/calibration")
    exp.add_argument("-n", type=int, default=30)
    cal = sub.add_parser("calibrate", help="Cohen's kappa: judge vs human consensus (>= 0.6)")
    cal.add_argument("--outputs", required=True)
    cal.add_argument("--human-a", required=True)
    cal.add_argument("--human-b", required=True)
    cal.add_argument("--judge-labels", help="skip judging and use this label file")
    cal.add_argument("--judge-models", default=os.environ.get("ORCHESTRIX_EVAL_JUDGE_MODELS"))
    return p


def main() -> None:
    configure_logging("WARNING")
    a = parser().parse_args()
    match a.command:
        case "run":
            code = asyncio.run(cmd_run(a))
        case "compare":
            code = cmd_compare(a)
        case "export-labels":
            code = cmd_export(a)
        case _:
            code = asyncio.run(cmd_calibrate(a))
    sys.exit(code)


if __name__ == "__main__":
    main()
