// Tasks dashboard (SPEC 18.1): status, cost, age; filters; search; cursor pagination.
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus, Search, SearchX } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "../components/Layout";
import { TaskList } from "../components/TaskRow";
import {
	Button,
	buttonClass,
	Empty,
	ErrorNote,
	Input,
	Select,
	Tabs,
} from "../components/ui";
import { api, type Schemas, unwrap } from "../lib/api/client";
import { hasRole, useAuth } from "../lib/auth";

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
type Scope = "all" | "mine";

export function TasksDashboard() {
	const { identity } = useAuth();
	const [status, setStatus] = useState<Status | "">("");
	const [scope, setScope] = useState<Scope>("all");
	const [search, setSearch] = useState("");
	const mine = scope === "mine";
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
	const needle = search.trim().toLowerCase();
	const shown = needle
		? rows.filter((t) => t.title.toLowerCase().includes(needle))
		: rows;

	return (
		<>
			<PageHeader
				title="Tasks"
				subtitle={
					query.data
						? `${rows.length}${query.hasNextPage ? "+" : ""} task${rows.length === 1 ? "" : "s"}`
						: undefined
				}
			>
				{hasRole(identity, "requester") && (
					<Link to="/tasks/new" className={buttonClass("primary")}>
						<Plus aria-hidden className="size-4" /> New task
					</Link>
				)}
			</PageHeader>
			<div className="mb-4 flex flex-wrap items-center gap-3">
				<Tabs
					label="Whose tasks"
					value={scope}
					onChange={setScope}
					options={[
						{ value: "all", label: "All" },
						{ value: "mine", label: "Mine" },
					]}
				/>
				<label htmlFor="status-filter" className="sr-only">
					Status
				</label>
				<Select
					id="status-filter"
					value={status}
					onChange={(e) => setStatus(e.target.value as Status | "")}
					className="w-52"
				>
					<option value="">All statuses</option>
					{STATUSES.map((s) => (
						<option key={s} value={s}>
							{s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ")}
						</option>
					))}
				</Select>
				<div className="relative ml-auto w-full sm:w-64">
					<Search
						aria-hidden
						className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted"
					/>
					<Input
						type="search"
						aria-label="Search loaded tasks by title"
						placeholder="Search"
						value={search}
						onChange={(e) => setSearch(e.target.value)}
						className="pl-9"
					/>
				</div>
			</div>
			<ErrorNote error={query.error} />
			{query.isPending ? (
				<div className="panel divide-y divide-border overflow-hidden">
					{[0, 1, 2, 3, 4].map((i) => (
						<div key={i} className="flex items-center gap-3 px-5 py-4">
							<div className="h-3.5 flex-1 animate-pulse rounded-full bg-fill" />
							<div className="h-5 w-20 animate-pulse rounded-full bg-fill" />
						</div>
					))}
				</div>
			) : shown.length === 0 ? (
				<div className="panel">
					{needle ? (
						<Empty icon={SearchX}>No loaded task matches “{search}”.</Empty>
					) : (
						<Empty>No tasks yet.</Empty>
					)}
				</div>
			) : (
				<TaskList tasks={shown} />
			)}
			{query.hasNextPage && (
				<div className="mt-5 text-center">
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
