# orchestrix_evals

Measures agent quality with real models (SPEC 20.2): golden tasks, planted defects, escalation,
injection and memory cases, and trajectory checks. Every case runs 3 times; reports give means
with bootstrap 95% confidence intervals. Requires the running stack (`make dev`) and, for the
VPN models, the Yotta VPN.

    uv run python -m orchestrix_evals run --suite smoke --models qwen3-8b-local
    uv run python -m orchestrix_evals run --suite full --models gpt-oss-120b --models qwen3-8b-local
    uv run python -m orchestrix_evals compare reports/a.json reports/b.json --out reports/compare.md
    uv run python -m orchestrix_evals export-labels reports/<run>.json --out reports/calibration
    uv run python -m orchestrix_evals calibrate --outputs reports/calibration/outputs.jsonl \
        --human-a reports/calibration/human_a.jsonl --human-b reports/calibration/human_b.jsonl

Datasets live in `src/orchestrix_evals/datasets/` (versioned YAML, attachments alongside).
The judge uses the `reviewer` role; pass `--judge-models` with the strongest model available, and
check it with `calibrate` (Cohen's kappa against two humans must be at least 0.6).
