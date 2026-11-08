"""Run the SPEC 5.6 showcase request against the running stack and print a live event feed.

    uv run python scripts/showcase.py [--api http://127.0.0.1:58100]

Uploads the latency CSV, submits the request, streams SSE events until the run ends, then
prints the final task status and subtask outcomes.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

CSV = Path(__file__).parents[1] / "tests" / "fixtures" / "showcase" / "query_latency.csv"
REQUEST = (
    "Compare the 4 most popular open-source vector databases on license, GitHub activity and "
    "published benchmark claims. Analyse the attached CSV of our query latencies. Write a 2-page "
    "recommendation, then email the summary to the platform team (platform-team@example.com)."
)
ROLES = ("planner", "specialist", "reviewer", "fast")
TERMINAL = {"completed", "failed", "cancelled", "budget_exceeded"}


def token(email: str, team: str, roles: str) -> str:
    """A dev JWT for a seeded demo user (`make seed`)."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to create a development token")
    out = subprocess.run(  # noqa: S603 -- executable is resolved locally; arguments are fixed.
        [
            uv,
            "run",
            "python",
            "-m",
            "orchestrix_api.security.dev_jwt",
            "--email",
            email,
            "--team",
            team,
            "--roles",
            roles,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip().splitlines()[-1]


def decide(
    api: httpx.Client, reviewer: dict[str, str], requester: dict[str, str], run_id: str
) -> None:
    """Answer every open escalation of the run the way the demo script says (SPEC 5.6 step 5)."""
    for who, headers in (("reviewer", reviewer), ("requester", requester)):
        inbox = api.get("/v1/escalations", headers=headers).raise_for_status().json()
        for esc in inbox:
            if esc["run_id"] != run_id or esc["status"] != "open":
                continue
            detail = (
                api.get(f"/v1/escalations/{esc['id']}", headers=headers).raise_for_status().json()
            )
            payload = detail["payload"]
            proposed = payload.get("proposed") or {}
            allowed = payload.get("allowed_actions", [])
            if payload["level"] == "clarify":
                if who != "requester":
                    continue
                body = {
                    "action": "answer",
                    "answers": ["Use your best judgement."] * len(payload.get("questions", [])),
                }
            elif proposed.get("tool") == "comms.send_email" and "approve_with_edits" in allowed:
                # A different user approves with an edited recipient list.
                body = {
                    "action": "approve_with_edits",
                    "edits": {"to": ["platform-team@orchestrix.local"]},
                }
            elif "approve" in allowed:
                body = {"action": "approve"}
            elif "approve_with_edits" in allowed and "budget" in proposed:
                raised = {k: v * 2 for k, v in proposed["budget"].items()}
                body = {"action": "approve_with_edits", "edits": raised}
            else:
                continue
            if who == "reviewer" and not detail.get("can_decide"):
                continue
            api.post(
                f"/v1/escalations/{esc['id']}/decision", json=body, headers=headers
            ).raise_for_status()
            print(f"          {who} decided {esc['trigger']}: {body['action']}", flush=True)


def describe(event: str, data: dict[str, object]) -> str:
    keys = (
        "subtask",
        "tool",
        "status",
        "verdict",
        "score",
        "version",
        "confidence",
        "reason",
        "trigger",
        "latency_ms",
    )
    detail = " ".join(f"{k}={data[k]}" for k in keys if data.get(k) is not None)
    return f"{event:<20} {detail}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:58100")
    parser.add_argument("--timeout-s", type=float, default=900)
    parser.add_argument(
        "--model",
        default="",
        help="one catalogue model for every chat role; empty = ORCHESTRIX_DEFAULT_MODELS",
    )
    args = parser.parse_args()
    headers = {
        "Authorization": f"Bearer {token('alice@orchestrix.local', 'platform', 'requester')}"
    }
    reviewer = {
        "Authorization": f"Bearer {token('bob@orchestrix.local', 'platform', 'requester,reviewer')}"
    }
    with httpx.Client(base_url=args.api, headers=headers, timeout=60) as api:
        upload = (
            api.post("/v1/uploads", files={"file": (CSV.name, CSV.read_bytes(), "text/csv")})
            .raise_for_status()
            .json()
        )
        created = (
            api.post(
                "/v1/tasks",
                json={
                    "title": "Vector DB recommendation",
                    "description": REQUEST,
                    "attachments": [upload["artifact_id"]],
                    "model_overrides": ({role: args.model for role in ROLES} if args.model else {}),
                },
            )
            .raise_for_status()
            .json()
        )
        print(f"task {created['task_id']}  run {created['run_id']}", flush=True)
        started = time.monotonic()
        detail: dict[str, Any] = {}
        while time.monotonic() - started < args.timeout_s:
            with api.stream("GET", f"/v1/runs/{created['run_id']}/events", timeout=60) as stream:
                block: dict[str, str] = {}
                for line in stream.iter_lines():
                    if line == "":
                        if "event" in block:
                            data = json.loads(block["data"])
                            print(
                                f"{time.monotonic() - started:7.1f}s  {describe(block['event'], data)}",
                                flush=True,
                            )
                            if block["event"] == "escalation.created":
                                decide(api, reviewer, headers, created["run_id"])
                        block = {}
                    elif ": " in line and not line.startswith(":"):
                        key, value = line.split(": ", 1)
                        block[key] = value
            detail = api.get(f"/v1/tasks/{created['task_id']}").json()
            if detail["status"] in TERMINAL:
                break
            time.sleep(1)
        else:
            raise TimeoutError(f"showcase did not finish within {args.timeout_s}s")
    print(f"\nstatus: {detail['status']}  failure: {detail['failure_reason']}")
    for s in detail["subtasks"]:
        print(
            f"  {s['key']:<4} {s['specialist']:<13} {s['status']:<10} attempts={s['attempts']} revisions={s['revisions']}"
        )
    sys.exit(0 if detail["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
