// One task in a grouped list: title, status, cost, age and a chevron, the whole row a link.
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import type { Schemas } from "../lib/api/client";
import { formatUsd, relativeTime } from "../lib/utils";
import { StatusBadge } from "./StatusBadge";

export function TaskRow({ task }: { task: Schemas["TaskSummary"] }) {
	return (
		<li>
			<Link
				to="/tasks/$taskId"
				params={{ taskId: task.id }}
				className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-fill/60 sm:px-5"
			>
				<span className="min-w-0 flex-1">
					<span className="block truncate text-sm font-medium">
						{task.title}
					</span>
					<span className="block text-xs text-muted">
						<time dateTime={task.created_at}>
							{relativeTime(task.created_at)}
						</time>
					</span>
				</span>
				<StatusBadge status={task.status} />
				<span className="hidden w-14 text-right text-sm text-muted tabular-nums sm:block">
					{formatUsd(task.cost_usd)}
				</span>
				<ChevronRight
					aria-hidden
					className="size-4 text-faint transition-transform group-hover:translate-x-0.5"
				/>
			</Link>
		</li>
	);
}

export function TaskList({ tasks }: { tasks: Schemas["TaskSummary"][] }) {
	return (
		<ul className="panel divide-y divide-border overflow-hidden">
			{tasks.map((t) => (
				<TaskRow key={t.id} task={t} />
			))}
		</ul>
	);
}
