// Replay (SPEC 18.1, 19.4; FR-70, FR-71; journey 5.4): checkpoints, state + patch, replay, diff.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { GitBranch } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "../components/Layout";
import {
	Button,
	Card,
	Empty,
	ErrorNote,
	Field,
	Input,
	JsonEditor,
	Kv,
	Spinner,
} from "../components/ui";
import { api, unwrap } from "../lib/api/client";
import { formatUsd } from "../lib/utils";

async function fetchDiff(runId: string, other: string) {
	return unwrap(
		await api.GET("/v1/runs/{run_id}/diff/{other_id}", {
			params: { path: { run_id: runId, other_id: other } },
		}),
	);
}
type DiffData = Awaited<ReturnType<typeof fetchDiff>>;

export function Replay() {
	const { runId } = useParams({ from: "/runs/$runId/replay" });
	const checkpoints = useQuery({
		queryKey: ["checkpoints", runId],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/checkpoints", {
					params: { path: { run_id: runId } },
				}),
			),
	});
	const [cid, setCid] = useState<string | null>(null);
	const state = useQuery({
		queryKey: ["checkpoint", runId, cid],
		enabled: !!cid,
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/checkpoints/{checkpoint_id}", {
					params: { path: { run_id: runId, checkpoint_id: cid ?? "" } },
				}),
			),
	});
	const [patch, setPatch] = useState("{}");
	const [asNode, setAsNode] = useState("");
	const [other, setOther] = useState("");
	const replay = useMutation({
		mutationFn: async () => {
			const parsed = JSON.parse(patch) as Record<string, unknown>;
			return unwrap(
				await api.POST("/v1/runs/{run_id}/replay", {
					params: { path: { run_id: runId } },
					body: {
						checkpoint_id: cid ?? "",
						state_patch: Object.keys(parsed).length ? parsed : null,
						as_node: asNode || null,
					},
				}),
			);
		},
		onSuccess: (r) => setOther(r.run_id),
	});
	const diff = useQuery({
		queryKey: ["diff", runId, other],
		enabled: false,
		queryFn: () => fetchDiff(runId, other),
	});
	let patchValid = true;
	try {
		JSON.parse(patch);
	} catch {
		patchValid = false;
	}

	return (
		<>
			<PageHeader title="Replay" />
			<div className="grid gap-4 xl:grid-cols-[22rem_1fr]">
				<Card title="Checkpoints (newest first)">
					<ErrorNote error={checkpoints.error} />
					{checkpoints.isPending ? (
						<Spinner />
					) : (checkpoints.data ?? []).length === 0 ? (
						<Empty>No checkpoints.</Empty>
					) : (
						<ol
							className="max-h-[36rem] space-y-1 overflow-y-auto text-sm"
							aria-label="Checkpoint timeline"
						>
							{checkpoints.data?.map((c) => (
								<li key={c.checkpoint_id}>
									<button
										type="button"
										onClick={() => setCid(c.checkpoint_id)}
										aria-current={cid === c.checkpoint_id || undefined}
										className={`w-full rounded px-2 py-1.5 text-left hover:bg-surface-2 ${cid === c.checkpoint_id ? "bg-surface-2 font-semibold" : ""}`}
									>
										step {c.step ?? "?"} · {c.source ?? ""} →{" "}
										{c.next.join(", ") || "end"}
										{c.interrupts > 0 && (
											<span className="ml-1 text-st-human">(paused)</span>
										)}
										<span className="block text-xs text-muted">
											{c.created_at
												? new Date(c.created_at).toLocaleString()
												: ""}
										</span>
									</button>
								</li>
							))}
						</ol>
					)}
				</Card>
				<div className="min-w-0 space-y-4">
					<Card title={cid ? `State at ${cid.slice(0, 12)}…` : "State"}>
						{!cid ? (
							<Empty>Pick a checkpoint.</Empty>
						) : state.isPending ? (
							<Spinner />
						) : (
							<>
								<ErrorNote error={state.error} />
								<pre className="max-h-80 overflow-auto rounded-md bg-surface-2 p-2 text-xs">
									{JSON.stringify(state.data?.values, null, 2)}
								</pre>
							</>
						)}
					</Card>
					{cid && (
						<Card title="Replay from here">
							<div className="space-y-3">
								<p className="text-sm text-muted">
									The original run is never changed. A patch writes a new
									checkpoint on a branch (for example raise a subtask's
									max_tool_calls in <code className="font-mono">plan</code>).
									Sensitive tools are simulated on replays.
								</p>
								<JsonEditor
									label="State patch (JSON object, optional)"
									value={patch}
									onChange={setPatch}
									rows={8}
								/>
								<Field
									label="Apply as node (optional)"
									hint="e.g. plan_gate; empty lets LangGraph decide"
								>
									{(id) => (
										<Input
											id={id}
											value={asNode}
											onChange={(e) => setAsNode(e.target.value)}
										/>
									)}
								</Field>
								<Button
									variant="primary"
									onClick={() => replay.mutate()}
									busy={replay.isPending}
									disabled={!patchValid}
								>
									<GitBranch aria-hidden className="size-4" /> Replay from here
								</Button>
								<ErrorNote error={replay.error} />
								{replay.data && (
									<p className="text-sm text-st-approved">
										Replay run {replay.data.run_id} queued.
									</p>
								)}
							</div>
						</Card>
					)}
					<Card title="Compare runs">
						<form
							className="flex flex-wrap items-end gap-2"
							onSubmit={(e) => {
								e.preventDefault();
								if (other) void diff.refetch();
							}}
						>
							<Field label="Other run id">
								{(id) => (
									<Input
										id={id}
										value={other}
										onChange={(e) => setOther(e.target.value)}
										className="w-96 font-mono text-xs"
									/>
								)}
							</Field>
							<Button type="submit" busy={diff.isFetching} disabled={!other}>
								Show diff
							</Button>
						</form>
						<ErrorNote error={diff.error} />
						{diff.data && <DiffView diff={diff.data} />}
					</Card>
				</div>
			</div>
		</>
	);
}

function Side({ side }: { side: DiffData["left"] }) {
	return (
		<Kv
			items={[
				["Run", `${side.kind} · ${side.status}`],
				["Tool calls", String(side.tool_calls)],
				["Tokens", String(side.tokens)],
				["Cost", formatUsd(side.cost_usd)],
			]}
		/>
	);
}

function UnifiedDiff({ text }: { text: string }) {
	if (!text) return <p className="text-xs text-muted">No differences.</p>;
	return (
		<pre className="max-h-72 overflow-auto rounded-md bg-surface-2 p-2 text-xs">
			{Array.from(text.split("\n").entries(), ([n, line]) => (
				<span
					key={`l${n}`}
					className={
						line.startsWith("+")
							? "text-st-approved"
							: line.startsWith("-")
								? "text-st-failed"
								: ""
					}
				>
					{line}
					{"\n"}
				</span>
			))}
		</pre>
	);
}

function DiffView({ diff }: { diff: DiffData }) {
	return (
		<div className="mt-4 space-y-4">
			<div className="grid gap-4 md:grid-cols-2">
				<Side side={diff.left} />
				<Side side={diff.right} />
			</div>
			<section>
				<h3 className="mb-1 text-sm font-semibold">Plan</h3>
				<UnifiedDiff text={diff.plan_diff} />
			</section>
			{diff.subtasks.map((s) => (
				<section key={s.key}>
					<h3 className="mb-1 text-sm font-semibold">
						{s.key}: {s.status[0] ?? "—"} → {s.status[1] ?? "—"} · scores [
						{(s.review_scores[0] ?? []).join(", ")}] → [
						{(s.review_scores[1] ?? []).join(", ")}]
					</h3>
					<UnifiedDiff text={s.output_diff} />
				</section>
			))}
		</div>
	);
}
