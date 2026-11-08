// Escalation inbox (SPEC 18.1, FR-46): grouped by level, SLA countdown.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Timer } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { Empty, ErrorNote, Spinner, Tabs } from "../components/ui";
import { api, type Schemas, unwrap } from "../lib/api/client";
import { keys } from "../lib/events";
import { relativeTime } from "../lib/utils";

const LEVELS = [
	"approve_plan",
	"approve_action",
	"take_over",
	"clarify",
	"notify",
] as const;
const LEVEL_LABEL: Record<string, string> = {
	approve_plan: "Approve plan",
	approve_action: "Approve action",
	take_over: "Take over",
	clarify: "Questions for you",
	notify: "For information",
};
type Filter = "open" | "resolved" | "expired";

export function useNow(intervalMs = 30_000): number {
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		const id = window.setInterval(() => setNow(Date.now()), intervalMs);
		return () => window.clearInterval(id);
	}, [intervalMs]);
	return now;
}

export function SlaCountdown({ due, now }: { due: string; now: number }) {
	const overdue = new Date(due).getTime() < now;
	return (
		<span
			className={`inline-flex items-center gap-1 text-xs ${overdue ? "font-semibold text-danger" : "text-muted"}`}
		>
			<Timer aria-hidden className="size-3.5" />
			{overdue ? "SLA passed " : "SLA "}
			<time dateTime={due}>{relativeTime(due, now)}</time>
		</span>
	);
}

export function Inbox() {
	const [filter, setFilter] = useState<Filter>("open");
	const now = useNow();
	const query = useQuery({
		queryKey: [...keys.escalations, filter],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/escalations", {
					params: { query: { status: filter } },
				}),
			),
		refetchInterval: 20_000,
	});
	const groups = new Map<string, Schemas["EscalationSummary"][]>();
	for (const e of query.data ?? [])
		groups.set(e.level, [...(groups.get(e.level) ?? []), e]);
	return (
		<>
			<PageHeader title="Escalations">
				<Tabs
					label="Escalation status"
					value={filter}
					onChange={setFilter}
					options={[
						{ value: "open", label: "Open" },
						{ value: "resolved", label: "Resolved" },
						{ value: "expired", label: "Expired" },
					]}
				/>
			</PageHeader>
			<ErrorNote error={query.error} />
			{query.isPending ? (
				<Spinner />
			) : (query.data ?? []).length === 0 ? (
				<Empty>Nothing waiting for you.</Empty>
			) : (
				<div className="space-y-5">
					{LEVELS.filter((l) => groups.has(l)).map((level) => (
						<section key={level} aria-labelledby={`lvl-${level}`}>
							<h2
								id={`lvl-${level}`}
								className="mb-2 px-1 text-xs font-semibold tracking-wide text-muted uppercase"
							>
								{LEVEL_LABEL[level]} ({groups.get(level)?.length})
							</h2>
							<ul className="panel divide-y divide-border overflow-hidden">
								{groups.get(level)?.map((e) => (
									<li key={e.id}>
										<Link
											to="/escalations/$id"
											params={{ id: e.id }}
											className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-surface-2"
										>
											<StatusBadge status={e.status} />
											<span className="min-w-0 flex-1">
												<span className="block font-medium">{e.title}</span>
												<span className="block text-xs text-muted">
													{e.task_title}
													{e.subtask ? ` · ${e.subtask}` : ""} ·{" "}
													{e.trigger.replace(/_/g, " ")}
												</span>
											</span>
											{e.status === "open" && (
												<SlaCountdown due={e.due_at} now={now} />
											)}
										</Link>
									</li>
								))}
							</ul>
						</section>
					))}
				</div>
			)}
		</>
	);
}
