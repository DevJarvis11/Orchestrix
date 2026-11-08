"""Judge calibration (SPEC 20.2): two humans label 30 golden outputs pass/fail; Cohen's kappa
between the judge and the human consensus must be >= 0.6 before the judge is trusted."""

import json
import random
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from orchestrix_evals.judge import Judge
from orchestrix_evals.models import load_suite
from orchestrix_evals.stats import cohens_kappa, consensus
from orchestrix_evals.suites import SuiteResult

KAPPA_REQUIRED = 0.6
Label = Literal["pass", "fail"]


class Output(BaseModel):
    id: str
    case: str
    request: str
    rubric: list[str]
    deliverable: str


class Labelled(BaseModel):
    id: str
    label: Label
    note: str = ""


class Calibration(BaseModel):
    n_outputs: int
    n_consensus: int  # outputs where both humans agree (disagreements are excluded)
    kappa_humans: float
    kappa_judge_vs_consensus: float
    required: float = KAPPA_REQUIRED
    trusted: bool
    disagreements: list[str] = Field(default=[])


def _read(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line.strip()]


def export_outputs(results: SuiteResult, out_dir: Path, n: int = 30, seed: int = 3) -> Path:
    """Write `outputs.jsonl` (what humans read) and two blank label templates."""
    golden = {g.id: g for g in load_suite().golden}
    pool = [
        Output(
            id=f"{c.id}#{r.run}",
            case=c.id,
            request=golden[c.id].description,
            rubric=golden[c.id].rubric,
            deliverable=r.deliverable,
        )
        for c in results.cases
        if c.id in golden
        for r in c.runs
        if r.deliverable
    ]
    random.Random(seed).shuffle(pool)  # noqa: S311 - sampling, not security
    chosen = pool[:n]
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = out_dir / "outputs.jsonl"
    outputs.write_text("".join(o.model_dump_json() + "\n" for o in chosen))
    for who in ("human_a", "human_b"):
        (out_dir / f"{who}.jsonl").write_text(
            "".join(
                json.dumps({"id": o.id, "label": "TODO pass|fail", "note": ""}) + "\n"
                for o in chosen
            )
        )
    return outputs


async def judge_outputs(judge: Judge, outputs: Path, out: Path) -> Path:
    labelled: list[Labelled] = []
    for line in _read(outputs):
        o = Output.model_validate_json(line)
        verdict = await judge.score(o.request, o.rubric, o.deliverable)
        labelled.append(
            Labelled(id=o.id, label="pass" if verdict.passed else "fail", note=f"{verdict.score:g}")
        )
    out.write_text("".join(item.model_dump_json() + "\n" for item in labelled))  # noqa: ASYNC240 - small local file
    return out


def kappa(judge_file: Path, human_a: Path, human_b: Path) -> Calibration:
    def labels(path: Path) -> dict[str, Label]:
        return {(item := Labelled.model_validate_json(line)).id: item.label for line in _read(path)}

    j, a, b = labels(judge_file), labels(human_a), labels(human_b)
    ids = sorted(set(j) & set(a) & set(b))
    if not ids:
        raise ValueError("no output id is labelled in all three files")
    agreed = consensus([a[i] for i in ids], [b[i] for i in ids])
    kept = [(i, c) for i, c in zip(ids, agreed, strict=True) if c is not None]
    k_judge = cohens_kappa([j[i] for i, _ in kept], [c for _, c in kept]) if kept else 0.0
    return Calibration(
        n_outputs=len(ids),
        n_consensus=len(kept),
        kappa_humans=cohens_kappa([a[i] for i in ids], [b[i] for i in ids]),
        kappa_judge_vs_consensus=k_judge,
        trusted=k_judge >= KAPPA_REQUIRED,
        disagreements=[i for i, c in zip(ids, agreed, strict=True) if c is None],
    )
