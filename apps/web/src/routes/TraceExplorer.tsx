// Trace Explorer (SPEC 18.1, 19.3; FR-61; journey 5.4): run -> plan -> subtasks -> calls and reviews.
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { FlowGraph, type FlowItem } from "../components/FlowGraph";
import { PageHeader } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { Button, Card, ErrorNote, Kv, Spinner } from "../components/ui";
import { api, type Schemas, unwrap } from "../lib/api/client";
import { keys } from "../lib/events";
import { formatTokens, formatUsd } from "../lib/utils";

type TraceNode = Schemas["TraceNode"];
const LOADABLE = new Set(["tool_call", "llm_call", "review", "subtask"]);

function flatten(root: TraceNode) {
	const items: { node: TraceNode; depth: number }[] = [];
	const edges: { source: string; target: string }[] = [];
	const walk = (n: TraceNode, depth: number) => {
		items.push({ node: n, depth });
		for (const c of n.children ?? []) {
			edges.push({ source: n.id, target: c.id });
			walk(c, depth + 1);
		}
	};
	walk(root, 0);
	return { items, edges };
}

export function TraceExplorer() {
	const { runId } = useParams({ from: "/runs/$runId/trace" });
	const trace = useQuery({
		queryKey: keys.trace(runId),
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/trace", {
					params: { path: { run_id: runId } },
				}),
			),
	});
	const [selected, setSelected] = useState<string | null>(null);
	const [detailsOnly, setDetailsOnly] = useState(false);
	const flat = useMemo(
		() => (trace.data ? flatten(trace.data.root) : { items: [], edges: [] }),
		[trace.data],
	);
	// Model calls are many and small: the graph shows them only on request.
	const graphItems = flat.items.filter(
		({ node }) => detailsOnly || node.kind !== "llm_call",
	);
	const ids = new Set(graphItems.map((i) => i.node.id));
	const node = flat.items.find((i) => i.node.id === selected)?.node ?? null;

	if (trace.isPending) return <Spinner />;
	if (trace.error) return <ErrorNote error={trace.error} />;
	const root = trace.data.root;
	return (
		<>
			<PageHeader title="Trace explorer">
				<span className="text-sm text-muted">
					{formatTokens(root.input_tokens + root.output_tokens)} tokens ·{" "}
					{formatUsd(root.cost_usd)}
				</span>
				<label className="flex items-center gap-2 text-sm">
					<input
						type="checkbox"
						className="size-4"
						checked={detailsOnly}
						onChange={(e) => setDetailsOnly(e.target.checked)}
					/>{" "}
					Show model calls in the graph
				</label>
				{trace.data.langfuse_url && (
					<a
						href={trace.data.langfuse_url}
						target="_blank"
						rel="noopener noreferrer"
						className="inline-flex items-center gap-1 text-sm text-accent hover:underline"
					>
						Open in Langfuse <ExternalLink aria-hidden className="size-3.5" />
					</a>
				)}
			</PageHeader>
			<div className="grid gap-4 xl:grid-cols-[1fr_26rem]">
				<div className="min-w-0 space-y-4">
					<FlowGraph
						items={graphItems.map(
							({ node: n }): FlowItem => ({
								id: n.id,
								title: n.label,
								subtitle: `${n.kind.replace(/_/g, " ")}${n.cost_usd ? ` · ${formatUsd(n.cost_usd)}` : ""}`,
								status: n.status,
							}),
						)}
						edges={flat.edges.filter(
							(e) => ids.has(e.source) && ids.has(e.target),
						)}
						selected={selected}
						onSelect={setSelected}
						height={520}
					/>
					<Card title="Tree">
						<ul
							className="max-h-96 overflow-y-auto text-sm"
							aria-label="Trace tree"
						>
							{flat.items.map(({ node: n, depth }) => (
								<li key={n.id} style={{ paddingLeft: depth * 16 }}>
									<button
										type="button"
										onClick={() => setSelected(n.id)}
										aria-current={selected === n.id || undefined}
										className={`flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-surface-2 ${selected === n.id ? "bg-surface-2 font-semibold" : ""}`}
									>
										{n.status && <StatusBadge status={n.status} />}
										<span className="truncate">{n.label}</span>
										<span className="ml-auto text-xs text-muted tabular-nums">
											{n.cost_usd ? formatUsd(n.cost_usd) : ""}
										</span>
									</button>
								</li>
							))}
						</ul>
					</Card>
				</div>
				<Inspector runId={runId} node={node} />
			</div>
			<p className="mt-4 text-sm">
				<Link
					to="/runs/$runId/replay"
					params={{ runId }}
					className="text-accent hover:underline"
				>
					Replay this run from a checkpoint
				</Link>
			</p>
		</>
	);
}

function Inspector({ runId, node }: { runId: string; node: TraceNode | null }) {
	const [load, setLoad] = useState<string | null>(null);
	const [kind, itemId] = node ? node.id.split(":") : [null, null];
	const loadable = !!node && LOADABLE.has(node.kind);
	const payload = useQuery({
		queryKey: ["trace-node", runId, node?.id],
		enabled: loadable && load === node?.id,
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/runs/{run_id}/trace/{kind}/{item_id}", {
					params: {
						path: {
							run_id: runId,
							kind: (kind ?? "tool_call") as
								| "tool_call"
								| "llm_call"
								| "review"
								| "subtask",
							item_id: itemId ?? "",
						},
					},
				}),
			),
	});
	if (!node) {
		return (
			<Card title="Inspector">
				<p className="text-sm text-muted">
					Select a node to see its prompt, response, tool I/O, tokens, latency
					and cost.
				</p>
			</Card>
		);
	}
	return (
		<Card title="Inspector">
			<div className="space-y-3">
				<div className="flex items-center gap-2">
					{node.status && <StatusBadge status={node.status} />}
					<span className="font-medium">{node.label}</span>
				</div>
				<Kv
					items={[
						["Kind", node.kind.replace(/_/g, " ")],
						[
							"Tokens",
							`${formatTokens(node.input_tokens)} in · ${formatTokens(node.output_tokens)} out`,
						],
						["Cost", formatUsd(node.cost_usd)],
						[
							"Latency",
							node.latency_ms != null ? `${node.latency_ms} ms` : "—",
						],
						[
							"Started",
							node.started_at
								? new Date(node.started_at).toLocaleString()
								: "—",
						],
					]}
				/>
				{node.payload && Object.keys(node.payload).length > 0 && (
					<pre className="max-h-72 overflow-auto rounded-md bg-surface-2 p-2 text-xs">
						{JSON.stringify(node.payload, null, 2)}
					</pre>
				)}
				{loadable && (
					<Button
						size="sm"
						onClick={() => setLoad(node.id)}
						busy={payload.isFetching}
					>
						Load full payload
					</Button>
				)}
				<ErrorNote error={payload.error} />
				{payload.data && load === node.id && (
					<pre className="max-h-[28rem] overflow-auto rounded-md bg-surface-2 p-2 text-xs">
						{JSON.stringify(payload.data.data, null, 2)}
					</pre>
				)}
			</div>
		</Card>
	);
}
