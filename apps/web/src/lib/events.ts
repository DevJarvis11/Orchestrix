// useRunEvents(runId): the one owner of a run's SSE stream (SPEC 17.2, 18.2).
// fetch-event-source sends the Authorization header (native EventSource cannot) and resends
// Last-Event-ID on reconnect; each event type patches or invalidates the query cache.
import { fetchEventSource } from "@microsoft/fetch-event-source";
import {
	type QueryClient,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { API_URL, type Schemas } from "./api/client";
import { getToken } from "./auth";

export type RunEvent = Schemas["RunEvent"];
export interface FeedItem {
	id: number;
	event: RunEvent;
}

const FINAL = new Set(["run.completed", "run.failed", "run.cancelled"]);

export const keys = {
	task: (taskId: string) => ["task", taskId] as const,
	tasks: ["tasks"] as const,
	graph: (runId: string) => ["run-graph", runId] as const,
	feed: (runId: string) => ["run-feed", runId] as const,
	efficiency: (runId: string) => ["efficiency", runId] as const,
	trace: (runId: string) => ["trace", runId] as const,
	escalations: ["escalations"] as const,
};

class Fatal extends Error {}

function apply(
	qc: QueryClient,
	runId: string,
	taskId: string | undefined,
	item: FeedItem,
) {
	qc.setQueryData<FeedItem[]>(keys.feed(runId), (old = []) =>
		old.some((x) => x.id === item.id)
			? old
			: [...old, item].sort((a, b) => a.id - b.id),
	);
	const ev = item.event;
	switch (ev.type) {
		case "subtask.started":
		case "subtask.status":
		case "subtask.result": {
			const status =
				ev.type === "subtask.started"
					? "running"
					: ev.type === "subtask.result"
						? "in_review"
						: ev.status;
			qc.setQueryData<Schemas["RunGraphView"]>(keys.graph(runId), (old) =>
				old && status
					? {
							...old,
							nodes: old.nodes.map((n) =>
								n.key === ev.subtask ? { ...n, status } : n,
							),
						}
					: old,
			);
			break;
		}
		case "plan.created":
		case "plan.edited":
		case "plan.approved":
		case "review.completed":
			void qc.invalidateQueries({ queryKey: keys.graph(runId) });
			break;
		case "llm.completed":
			void qc.invalidateQueries({ queryKey: keys.efficiency(runId) });
			break;
		case "escalation.created":
		case "escalation.resolved":
			void qc.invalidateQueries({ queryKey: keys.escalations });
			break;
		default:
			break;
	}
	if (
		ev.type.startsWith("run.") ||
		ev.type === "deliverable.ready" ||
		ev.type.startsWith("escalation.")
	) {
		if (taskId) void qc.invalidateQueries({ queryKey: keys.task(taskId) });
		void qc.invalidateQueries({ queryKey: keys.tasks });
	}
}

export function useRunEvents(
	runId: string | null | undefined,
	taskId?: string,
) {
	const qc = useQueryClient();
	const [connected, setConnected] = useState(false);
	const feed = useQuery<FeedItem[]>({
		queryKey: keys.feed(runId ?? "none"),
		queryFn: () => [],
		staleTime: Number.POSITIVE_INFINITY,
		enabled: false,
	});

	useEffect(() => {
		if (!runId) return;
		const ctrl = new AbortController();
		void fetchEventSource(`${API_URL}/v1/runs/${runId}/events`, {
			signal: ctrl.signal,
			openWhenHidden: true,
			headers: { Authorization: `Bearer ${getToken() ?? ""}` },
			async onopen(response) {
				if (
					response.status === 401 ||
					response.status === 403 ||
					response.status === 404
				) {
					throw new Fatal(`event stream refused: HTTP ${response.status}`);
				}
				if (!response.ok) throw new Error(`HTTP ${response.status}`);
				setConnected(true);
			},
			onmessage(msg) {
				if (!msg.data || !msg.id) return; // keepalive comments carry no data
				const event = JSON.parse(msg.data) as RunEvent;
				apply(qc, runId, taskId, { id: Number(msg.id), event });
				if (FINAL.has(event.type)) ctrl.abort(); // the server closes after these too
			},
			onclose() {
				setConnected(false);
				if (!ctrl.signal.aborted)
					throw new Error("stream closed; reconnecting"); // retry with Last-Event-ID
			},
			onerror(err) {
				setConnected(false);
				if (err instanceof Fatal) throw err; // stop retrying
				return 2000; // back off, then reconnect
			},
		}).catch(() => setConnected(false));
		return () => ctrl.abort();
	}, [runId, taskId, qc]);

	return { events: feed.data ?? [], connected };
}

export function describeEvent(ev: RunEvent): string {
	switch (ev.type) {
		case "subtask.started":
			return `${ev.subtask} started (${ev.specialist ?? ""})`;
		case "subtask.status":
			return `${ev.subtask} → ${ev.status ?? ""}`;
		case "subtask.result":
			return `${ev.subtask} produced a result`;
		case "tool.called":
			return `${ev.subtask ?? ""} called ${ev.tool}`;
		case "tool.completed":
			return `${ev.tool} ${ev.status ?? ""}${ev.latency_ms != null ? ` in ${ev.latency_ms} ms` : ""}`;
		case "tool.denied":
			return `${ev.tool} denied: ${ev.reason ?? ""}`;
		case "review.completed":
			return `review ${ev.subtask ?? "final"}: ${ev.verdict} (${ev.score}/10)`;
		case "llm.completed":
			return `${ev.agent} via ${ev.model_id}: ${ev.input_tokens + ev.output_tokens} tokens`;
		case "plan.created":
		case "plan.edited":
		case "plan.approved":
			return `plan v${ev.version} ${ev.type.split(".")[1]}${ev.confidence != null ? ` (confidence ${ev.confidence.toFixed(2)})` : ""}`;
		case "plan.invalid":
			return `plan v${ev.version} invalid: ${ev.errors.join("; ")}`;
		case "escalation.created":
			return `escalation: ${ev.trigger} (${ev.level})`;
		case "escalation.resolved":
			return `resolved by ${ev.actor ?? "a reviewer"}: ${ev.action ?? ""}`;
		case "budget.warning":
		case "budget.exceeded":
			return `budget ${ev.type.split(".")[1]}: ${ev.metric} ${ev.used}/${ev.cap}`;
		case "memory.retrieved":
		case "memory.written":
			return `${ev.type.replace(".", " ")}: ${ev.memory_ids.length}`;
		case "deliverable.ready":
			return `deliverable ready (${ev.chars} chars)`;
		default:
			return `${ev.type}${"reason" in ev && ev.reason ? `: ${ev.reason}` : ""}`;
	}
}
