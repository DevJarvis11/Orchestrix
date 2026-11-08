// Tasks dashboard (SPEC 18.1 "/"): status, cost, age, owner; filters; cursor pagination.
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { Button, Empty, ErrorNote, Select, Spinner } from "../components/ui";
import { api, type Schemas, unwrap } from "../lib/api/client";
import { hasRole, useAuth } from "../lib/auth";
import { formatUsd, relativeTime, shortId } from "../lib/utils";

type Status = NonNullable<Schemas["TaskSummary"]["status"]>;
const STATUSES: Status[] = [
	"queued",
	"planning",
	"awaiting_plan_approval",
	"running",
	"awaiting_human",
	"reviewing",
	"completed",
	"failed",
	"cancelled",
	"budget_exceeded",
];

export function TasksDashboard() {
	const { identity } = useAuth();
	const [status, setStatus] = useState<Status | "">("");
	const [mine, setMine] = useState(false);
	const query = useInfiniteQuery({
		queryKey: ["tasks", status, mine],
		initialPageParam: null as string | null,
		queryFn: async ({ pageParam }) =>
			unwrap(
				await api.GET("/v1/tasks", {
					params: {
						query: {
							status: status || null,
							mine,
							cursor: pageParam,
							limit: 25,
						},
					},
				}),
			),
		getNextPageParam: (last) => last.next_cursor ?? null,
		refetchInterval: 15_000,
	});
	const rows = query.data?.pages.flatMap((p) => p.items) ?? [];
	return (
		<>
			<PageHeader title="Tasks">
				{hasRole(identity, "requester") && (
					<Link
						to="/tasks/new"
						className="inline-flex h-9 items-center gap-1.5 rounded-md bg-accent px-3.5 text-sm font-medium text-accent-fg"
					>
						<Plus aria-hidden className="size-4" /> New task
					</Link>
				)}
			</PageHeader>
			<div className="mb-3 flex flex-wrap items-end gap-3">
				<label className="text-sm" htmlFor="status-filter">
					<span className="mb-1 block text-muted">Status</span>
					<Select
						id="status-filter"
						value={status}
						onChange={(e) => setStatus(e.target.value as Status | "")}
						className="w-56"
					>
						<option value="">All statuses</option>
						{STATUSES.map((s) => (
							<option key={s} value={s}>
								{s.replace(/_/g, " ")}
							</option>
						))}
					</Select>
				</label>
				<label className="flex h-9 items-center gap-2 text-sm">
					<input
						type="checkbox"
						checked={mine}
						onChange={(e) => setMine(e.target.checked)}
						className="size-4"
					/>
					Only my tasks
				</label>
			</div>
			<ErrorNote error={query.error} />
			{query.isPending ? (
				<Spinner />
			) : rows.length === 0 ? (
				<Empty>No tasks yet.</Empty>
			) : (
				<div className="overflow-x-auto rounded-lg border border-border bg-surface">
					<table className="w-full text-sm">
						<caption className="sr-only">Tasks</caption>
						<thead className="border-b border-border text-left text-muted">
							<tr>
								<th scope="col" className="px-3 py-2 font-medium">
									Title
								</th>
								<th scope="col" className="px-3 py-2 font-medium">
									Status
								</th>
								<th scope="col" className="px-3 py-2 font-medium">
									Cost
								</th>
								<th scope="col" className="px-3 py-2 font-medium">
									Age
								</th>
								<th scope="col" className="px-3 py-2 font-medium">
									Owner
								</th>
							</tr>
						</thead>
						<tbody>
							{rows.map((t) => (
								<tr
									key={t.id}
									className="border-b border-border last:border-0 hover:bg-surface-2"
								>
									<td className="px-3 py-2">
										<Link
											to="/tasks/$taskId"
											params={{ taskId: t.id }}
											className="font-medium hover:underline"
										>
											{t.title}
										</Link>
									</td>
									<td className="px-3 py-2">
										<StatusBadge status={t.status} />
									</td>
									<td className="px-3 py-2 tabular-nums">
										{formatUsd(t.cost_usd)}
									</td>
									<td className="px-3 py-2 text-muted">
										<time dateTime={t.created_at}>
											{relativeTime(t.created_at)}
										</time>
									</td>
									<td className="px-3 py-2 font-mono text-xs text-muted">
										{shortId(t.requester_id)}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
			{query.hasNextPage && (
				<div className="mt-3 text-center">
					<Button
						onClick={() => void query.fetchNextPage()}
						busy={query.isFetchingNextPage}
					>
						Load more
					</Button>
				</div>
			)}
		</>
	);
}
