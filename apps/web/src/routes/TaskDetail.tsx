// Task detail (SPEC 18.1, journey 5.1): live plan DAG, event feed, subtask drawer, deliverable,
// cost panel with the A6 efficiency breakdown, cancel.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import {
	Activity,
	AlertTriangle,
	Ban,
	Download,
	History,
	Network,
} from "lucide-react";
import { useState } from "react";
import { FlowGraph } from "../components/FlowGraph";
import { PageHeader } from "../components/Layout";
import { Markdown } from "../components/Markdown";
import { StatusBadge } from "../components/StatusBadge";
import {
	Button,
	buttonClass,
	Card,
	Empty,
	ErrorNote,
	Kv,
	Modal,
	Spinner,
} from "../components/ui";
import { api, authedFetch, type Schemas, unwrap } from "../lib/api/client";
import { hasRole, useAuth } from "../lib/auth";
import { describeEvent, keys, useRunEvents } from "../lib/events";
import { formatTokens, formatUsd, relativeTime } from "../lib/utils";

const TERMINAL = new Set([
	"completed",
	"failed",
	"cancelled",
	"budget_exceeded",
]);

export function TaskDetail() {
	const { taskId } = useParams({ from: "/tasks/$taskId" });
	const { identity } = useAuth();
	const qc = useQueryClient();
	const task = useQuery({
		queryKey: keys.task(taskId),
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/tasks/{task_id}", {
					params: { path: { task_id: taskId } },
				}),
			),
	});
	const runId = task.data?.run_id ?? null;
	const { events, connected } = useRunEvents(runId, taskId);
	const graph = useQuery({
		queryKey: keys.graph(runId ?? ""),
		enabled: !!runId,
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/graph", {
					params: { path: { run_id: runId ?? "" } },
				}),
			),
	});
	const efficiency = useQuery({
		queryKey: keys.efficiency(runId ?? ""),
		enabled: !!runId,
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/efficiency", {
					params: { path: { run_id: runId ?? "" } },
				}),
			),
	});
	const cancel = useMutation({
		mutationFn: async () =>
			unwrap(
				await api.POST("/v1/tasks/{task_id}/cancel", {
					params: { path: { task_id: taskId } },
				}),
			),
		onSuccess: () => void qc.invalidateQueries({ queryKey: keys.task(taskId) }),
	});
	const [selected, setSelected] = useState<string | null>(null);

	if (task.isPending) return <Spinner />;
	if (task.error) return <ErrorNote error={task.error} />;
	const t = task.data;
	const nodes = graph.data?.nodes ?? [];
	const canCancel = !TERMINAL.has(t.status); // the API allows only the owner or an operator

	return (
		<>
			<PageHeader title={t.title}>
				<StatusBadge status={t.status} />
				{runId && (
					<Link
						to="/runs/$runId/trace"
						params={{ runId }}
						className={buttonClass()}
					>
						<Network aria-hidden className="size-4" /> Trace
					</Link>
				)}
				{runId && hasRole(identity, "operator") && (
					<Link
						to="/runs/$runId/replay"
						params={{ runId }}
						className={buttonClass()}
					>
						<History aria-hidden className="size-4" /> Replay
					</Link>
				)}
				{canCancel && (
					<Button
						variant="danger"
						onClick={() => cancel.mutate()}
						busy={cancel.isPending}
					>
						<Ban aria-hidden className="size-4" /> Cancel
					</Button>
				)}
			</PageHeader>
			<ErrorNote error={cancel.error} />
			{t.failure_reason && (
				<p
					role="alert"
					className="mb-3 flex items-center gap-2 rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm text-danger"
				>
					<AlertTriangle aria-hidden className="size-4" /> {t.failure_reason}
				</p>
			)}
			<div className="grid gap-4 xl:grid-cols-[1fr_24rem]">
				<div className="min-w-0 space-y-4">
					<Card
						title={`Plan${t.plan_version ? ` v${t.plan_version}` : ""}`}
						actions={
							t.plan && (
								<span className="text-xs text-muted">
									confidence {t.plan.confidence.toFixed(2)} —{" "}
									{t.plan.confidence_reason}
								</span>
							)
						}
					>
						{nodes.length === 0 ? (
							<Empty>
								{t.status === "queued" || t.status === "planning"
									? "Planning…"
									: "No plan."}
							</Empty>
						) : (
							<FlowGraph
								items={nodes.map((n) => ({
									id: n.key,
									title: `${n.key} · ${n.specialist}`,
									subtitle: n.description,
									status: n.status,
									extra:
										n.revisions > 0 ? (
											<span className="text-xs text-muted">
												rev {n.revisions}
											</span>
										) : undefined,
								}))}
								edges={graph.data?.edges ?? []}
								selected={selected}
								onSelect={setSelected}
							/>
						)}
					</Card>
					<Deliverable taskId={taskId} artifactId={t.deliverable_artifact_id} />
				</div>
				<div className="space-y-4">
					<CostPanel efficiency={efficiency.data} />
					<Card
						title="Live events"
						actions={
							<span className="flex items-center gap-1.5 text-xs text-muted">
								<span className="relative flex size-2">
									{connected && (
										<span className="absolute inline-flex size-full animate-ping rounded-full bg-st-approved opacity-60" />
									)}
									<span
										className={`relative inline-flex size-2 rounded-full ${connected ? "bg-st-approved" : "bg-faint"}`}
									/>
								</span>
								<Activity aria-hidden className="size-3.5" />
								{connected ? "Live" : "Idle"}
							</span>
						}
					>
						<ol
							aria-live="polite"
							className="max-h-[32rem] overflow-y-auto pl-1 text-sm"
						>
							{events.length === 0 && (
								<li className="text-muted">No events yet.</li>
							)}
							{[...events].reverse().map(({ id, event }) => (
								<li
									key={id}
									className="relative border-l border-border pb-4 pl-4 last:border-transparent last:pb-0"
								>
									<span
										aria-hidden
										className="absolute top-1 -left-[5px] size-2.5 rounded-full bg-accent ring-4 ring-surface"
									/>
									<span className="block font-mono text-[11px] text-muted">
										{event.type}
									</span>
									{describeEvent(event)}
									{event.ts && (
										<time
											className="block text-xs text-muted"
											dateTime={event.ts}
										>
											{relativeTime(event.ts)}
										</time>
									)}
								</li>
							))}
						</ol>
					</Card>
				</div>
			</div>
			{runId && (
				<SubtaskDrawer
					runId={runId}
					subtaskKey={selected}
					node={nodes.find((n) => n.key === selected)}
					onClose={() => setSelected(null)}
				/>
			)}
		</>
	);
}

function CostPanel({
	efficiency,
}: {
	efficiency: Schemas["Efficiency"] | undefined;
}) {
	if (!efficiency) return null;
	const e = efficiency;
	return (
		<Card
			title="Cost"
			actions={
				e.fallbacks.calls > 0 && (
					<span className="inline-flex items-center gap-1 rounded-full bg-st-human/12 px-2 py-0.5 text-xs text-st-human">
						<AlertTriangle aria-hidden className="size-3.5" /> fell back on{" "}
						{e.fallbacks.calls} call{e.fallbacks.calls > 1 ? "s" : ""}
					</span>
				)
			}
		>
			<Kv
				items={[
					[
						"Total",
						`${formatUsd(e.total.cost_usd)}${e.notional ? " (notional)" : ""}`,
					],
					[
						"Tokens",
						`${formatTokens(e.total.input_tokens)} in · ${formatTokens(e.total.output_tokens)} out`,
					],
					["Model calls", String(e.total.calls)],
					[
						"Revisions",
						`${formatUsd(e.revisions.cost_usd)} · ${e.revisions.calls} calls`,
					],
					[
						"Retries",
						`${formatUsd(e.retries.cost_usd)} · ${e.retries.calls} calls`,
					],
					[
						"Fallbacks",
						`${formatUsd(e.fallbacks.cost_usd)} · ${e.fallbacks.calls} calls`,
					],
					[
						"Served by",
						e.by_model.map((b) => `${b.key} (${b.calls})`).join(", ") || "—",
					],
				]}
			/>
			<details className="mt-3 text-sm">
				<summary className="cursor-pointer text-muted">
					Breakdown by subtask and purpose
				</summary>
				<table className="mt-2 w-full text-xs">
					<caption className="sr-only">Cost by subtask</caption>
					<tbody>
						{[
							...e.by_subtask.map((b) => ({ ...b, id: `subtask:${b.key}` })),
							...e.by_purpose.map((b) => ({ ...b, id: `purpose:${b.key}` })),
						].map((b) => (
							<tr key={b.id} className="border-b border-border">
								<th scope="row" className="py-1 text-left font-normal">
									{b.key}
								</th>
								<td className="py-1 text-right tabular-nums">
									{formatTokens(b.input_tokens + b.output_tokens)}
								</td>
								<td className="py-1 text-right tabular-nums">
									{formatUsd(b.cost_usd)}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</details>
		</Card>
	);
}

function Deliverable({
	taskId,
	artifactId,
}: {
	taskId: string;
	artifactId: string | null;
}) {
	const doc = useQuery({
		queryKey: ["deliverable", taskId, artifactId],
		enabled: !!artifactId,
		// 302 to a presigned S3 URL; fetch drops Authorization on the cross-origin redirect.
		queryFn: async () => {
			const r = await authedFetch(`/v1/tasks/${taskId}/deliverable`);
			if (!r.ok) throw new Error(`deliverable: HTTP ${r.status}`);
			return { text: await r.text(), url: r.url };
		},
	});
	if (!artifactId) return null;
	return (
		<Card
			title="Deliverable"
			actions={
				doc.data && (
					<a
						href={doc.data.url}
						download="deliverable.md"
						className={buttonClass("secondary", "sm")}
					>
						<Download aria-hidden className="size-4" /> Download
					</a>
				)
			}
		>
			<ErrorNote error={doc.error} />
			{doc.isPending ? (
				<Spinner />
			) : (
				doc.data && <Markdown>{doc.data.text}</Markdown>
			)}
		</Card>
	);
}

function findNode(
	node: Schemas["TraceNode"],
	pred: (n: Schemas["TraceNode"]) => boolean,
): Schemas["TraceNode"] | null {
	if (pred(node)) return node;
	for (const c of node.children ?? []) {
		const hit = findNode(c, pred);
		if (hit) return hit;
	}
	return null;
}

function SubtaskDrawer({
	runId,
	subtaskKey,
	node,
	onClose,
}: {
	runId: string;
	subtaskKey: string | null;
	node: Schemas["GraphNode"] | undefined;
	onClose: () => void;
}) {
	const trace = useQuery({
		queryKey: keys.trace(runId),
		enabled: !!subtaskKey,
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/trace", {
					params: { path: { run_id: runId } },
				}),
			),
	});
	const traceNode =
		trace.data && subtaskKey
			? findNode(
					trace.data.root,
					(n) => n.kind === "subtask" && n.label.startsWith(`${subtaskKey} `),
				)
			: null;
	const rowId = traceNode?.id.split(":")[1] ?? null;
	const row = useQuery({
		queryKey: ["subtask-row", runId, rowId],
		enabled: !!rowId,
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/trace/{kind}/{item_id}", {
					params: {
						path: { run_id: runId, kind: "subtask", item_id: rowId ?? "" },
					},
				}),
			),
	});
	const result = (row.data?.data.result ?? null) as {
		output?: string;
		sources?: string[];
		notes?: string;
		confidence?: number;
	} | null;
	const reviews = (traceNode?.children ?? []).filter(
		(c) => c.kind === "review",
	);
	return (
		<Modal
			open={!!subtaskKey}
			onClose={onClose}
			title={subtaskKey ? `Subtask ${subtaskKey}` : "Subtask"}
			side
		>
			{node && (
				<div className="mb-4 space-y-2">
					<StatusBadge status={node.status} />
					<p className="text-sm">{node.description}</p>
					<Kv
						items={[
							["Specialist", node.specialist],
							["Attempts", String(node.attempts)],
							["Revisions", String(node.revisions)],
							["Output", node.expected_output],
						]}
					/>
				</div>
			)}
			{(trace.isPending || row.isPending) && rowId !== null && <Spinner />}
			<ErrorNote error={trace.error ?? row.error} />
			{result?.output ? (
				<section className="space-y-3">
					<h3 className="font-semibold">Result</h3>
					<Markdown>{result.output}</Markdown>
					{result.sources && result.sources.length > 0 && (
						<>
							<h3 className="font-semibold">Sources</h3>
							<ul className="list-disc pl-5 text-sm">
								{result.sources.map((s) => (
									<li key={s} className="break-all">
										{s.startsWith("http") ? (
											<a
												href={s}
												target="_blank"
												rel="noopener noreferrer nofollow"
												className="text-accent hover:underline"
											>
												{s}
											</a>
										) : (
											s
										)}
									</li>
								))}
							</ul>
						</>
					)}
					{result.notes && (
						<p className="whitespace-pre-wrap rounded-xl bg-surface-2 p-3 text-sm">
							{result.notes}
						</p>
					)}
				</section>
			) : (
				<Empty>No result yet.</Empty>
			)}
			{reviews.length > 0 && (
				<section className="mt-4 space-y-2">
					<h3 className="font-semibold">Reviews</h3>
					{reviews.map((r) => (
						<div key={r.id} className="rounded-xl bg-surface-2 p-3 text-sm">
							<StatusBadge status={r.status} />{" "}
							<span className="ml-1">{r.label}</span>
							<p className="mt-1 whitespace-pre-wrap text-muted">
								{String(r.payload?.feedback ?? "")}
							</p>
						</div>
					))}
				</section>
			)}
		</Modal>
	);
}
