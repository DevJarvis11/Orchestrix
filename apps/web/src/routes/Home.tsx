// Home: ask for an outcome in one box, then see what is running, what waits for you, and recent work.
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
	ArrowUp,
	CircleDollarSign,
	FileText,
	Hand,
	Loader2,
	Paperclip,
	PlayCircle,
	SlidersHorizontal,
	Sparkles,
	X,
} from "lucide-react";
import { motion, type Variants } from "motion/react";
import { type KeyboardEvent, useRef, useState } from "react";
import { useOpenEscalations } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { TaskList } from "../components/TaskRow";
import { Empty, ErrorNote, Stat } from "../components/ui";
import { api, type Schemas, unwrap } from "../lib/api/client";
import { hasRole, useAuth } from "../lib/auth";
import { keys } from "../lib/events";
import { EXTENSIONS, MAX_FILES, useUploads } from "../lib/uploads";
import {
	cn,
	formatUsd,
	greeting,
	relativeTime,
	titleFromPrompt,
	uuid,
} from "../lib/utils";

const rise: Variants = {
	hidden: { opacity: 0, y: 12 },
	show: (i: number) => ({
		opacity: 1,
		y: 0,
		transition: { delay: 0.04 * i, duration: 0.4, ease: [0.22, 1, 0.36, 1] },
	}),
};

function AskBox() {
	const navigate = useNavigate();
	const [text, setText] = useState("");
	const files = useUploads();
	const fileInput = useRef<HTMLInputElement>(null);

	const create = useMutation({
		mutationFn: async () => {
			const body: Schemas["TaskCreate"] = {
				title: titleFromPrompt(text),
				description: text.trim(),
				attachments: files.artifactIds,
				require_plan_approval: false,
				require_final_approval: false,
				model_overrides: {},
				budget: null,
			};
			return unwrap(
				await api.POST("/v1/tasks", {
					body,
					params: { header: { "Idempotency-Key": uuid() } },
				}),
			);
		},
		onSuccess: (created) =>
			void navigate({
				to: "/tasks/$taskId",
				params: { taskId: created.task_id },
			}),
	});

	const ready = text.trim().length > 0 && !files.busy && !create.isPending;
	const submit = () => ready && create.mutate();
	const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
		if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
			e.preventDefault();
			submit();
		}
	};

	return (
		<form
			onSubmit={(e) => {
				e.preventDefault();
				submit();
			}}
		>
			<div className="panel p-2 shadow-pop transition-shadow focus-within:ring-4 focus-within:ring-accent/15">
				<label htmlFor="ask" className="sr-only">
					Describe what you want done
				</label>
				<textarea
					id="ask"
					value={text}
					onChange={(e) => setText(e.target.value)}
					onKeyDown={onKey}
					maxLength={8000}
					rows={3}
					placeholder="Research the top 5 open-source vector databases, compare licensing and benchmarks, and write a 2-page recommendation…"
					className="field-sizing-content max-h-72 min-h-20 w-full resize-none bg-transparent px-3 pt-2 text-[15px] leading-relaxed placeholder:text-faint focus:outline-none"
				/>
				{files.uploads.length > 0 && (
					<ul className="flex flex-wrap gap-1.5 px-2 pb-2">
						{files.uploads.map((u) => (
							<li
								key={u.id}
								className={cn(
									"inline-flex items-center gap-1.5 rounded-full bg-fill py-1 pr-1 pl-2.5 text-xs",
									u.error && "bg-danger/10 text-danger",
								)}
							>
								{u.artifactId || u.error ? (
									<FileText aria-hidden className="size-3.5" />
								) : (
									<Loader2 aria-hidden className="size-3.5 animate-spin" />
								)}
								<span className="max-w-40 truncate">{u.name}</span>
								{u.error && <span>· {u.error}</span>}
								<button
									type="button"
									aria-label={`Remove ${u.name}`}
									onClick={() => files.remove(u.id)}
									className="grid size-5 place-items-center rounded-full hover:bg-fill"
								>
									<X aria-hidden className="size-3" />
								</button>
							</li>
						))}
					</ul>
				)}
				<div className="flex items-center gap-1 px-1">
					<input
						ref={fileInput}
						type="file"
						multiple
						accept={EXTENSIONS.join(",")}
						className="sr-only"
						tabIndex={-1}
						onChange={(e) => {
							if (e.target.files) void files.add(e.target.files);
							e.target.value = "";
						}}
					/>
					<button
						type="button"
						onClick={() => fileInput.current?.click()}
						disabled={files.uploads.length >= MAX_FILES}
						aria-label="Attach files"
						title="Attach pdf, md, txt, csv or xlsx (up to 5, 10 MB each)"
						className="grid size-9 place-items-center rounded-full text-muted transition hover:bg-fill hover:text-fg disabled:opacity-40"
					>
						<Paperclip aria-hidden className="size-[18px]" />
					</button>
					<Link
						to="/tasks/new"
						search={{ q: text || undefined }}
						className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[13px] text-muted transition hover:bg-fill hover:text-fg"
					>
						<SlidersHorizontal aria-hidden className="size-4" />
						More options
					</Link>
					<span className="ml-auto hidden pr-1 text-xs text-faint sm:inline">
						Enter to start · Shift+Enter for a new line
					</span>
					<motion.button
						type="submit"
						disabled={!ready}
						aria-label="Start task"
						whileTap={{ scale: 0.9 }}
						className="ml-auto grid size-9 place-items-center rounded-full bg-accent text-accent-fg shadow-sm transition disabled:bg-fill disabled:text-faint disabled:shadow-none"
					>
						{create.isPending ? (
							<Loader2 aria-hidden className="size-[18px] animate-spin" />
						) : (
							<ArrowUp aria-hidden className="size-[18px]" strokeWidth={2.4} />
						)}
					</motion.button>
				</div>
			</div>
			<div className="mt-3">
				<ErrorNote error={create.error} />
			</div>
		</form>
	);
}

function RunningCard({
	task,
	index,
}: {
	task: Schemas["TaskSummary"];
	index: number;
}) {
	return (
		<motion.li variants={rise} initial="hidden" animate="show" custom={index}>
			<Link
				to="/tasks/$taskId"
				params={{ taskId: task.id }}
				className="panel block h-full p-4 transition hover:-translate-y-0.5 hover:shadow-pop"
			>
				<StatusBadge status={task.status} />
				<p className="mt-2.5 line-clamp-2 text-[15px] font-semibold tracking-tight">
					{task.title}
				</p>
				<div className="mt-3 h-1 overflow-hidden rounded-full bg-fill">
					<div className="bar-indeterminate h-full w-2/5 rounded-full bg-accent" />
				</div>
				<p className="mt-2 text-xs text-muted">
					{formatUsd(task.cost_usd)} · started{" "}
					<time dateTime={task.created_at}>
						{relativeTime(task.created_at)}
					</time>
				</p>
			</Link>
		</motion.li>
	);
}

function WaitingCard({
	item,
	index,
}: {
	item: Schemas["EscalationSummary"];
	index: number;
}) {
	return (
		<motion.li variants={rise} initial="hidden" animate="show" custom={index}>
			<Link
				to="/escalations/$id"
				params={{ id: item.id }}
				className="panel block h-full p-4 transition hover:-translate-y-0.5 hover:shadow-pop"
			>
				<span className="inline-flex items-center gap-1 rounded-full bg-st-human/12 px-2.5 py-0.5 text-xs font-medium text-st-human">
					<Hand aria-hidden className="size-3.5" />
					Needs you
				</span>
				<p className="mt-2.5 line-clamp-2 text-[15px] font-semibold tracking-tight">
					{item.title}
				</p>
				<p className="mt-1 truncate text-xs text-muted">{item.task_title}</p>
				<div className="mt-3 h-1 overflow-hidden rounded-full bg-fill">
					<div className="h-full w-1/3 rounded-full bg-st-human" />
				</div>
				<p className="mt-2 text-xs text-muted">
					Due <time dateTime={item.due_at}>{relativeTime(item.due_at)}</time>
				</p>
			</Link>
		</motion.li>
	);
}

function SectionTitle({
	children,
	to,
}: {
	children: string;
	to?: "/tasks" | "/escalations";
}) {
	return (
		<div className="mt-10 mb-3 flex items-baseline justify-between px-1">
			<h2 className="text-[17px] font-semibold tracking-tight">{children}</h2>
			{to && (
				<Link
					to={to}
					className="text-[13px] font-medium text-accent hover:underline"
				>
					See all
				</Link>
			)}
		</div>
	);
}

export function Home() {
	const { identity } = useAuth();
	const operator = hasRole(identity, "operator");
	const running = useQuery({
		queryKey: [...keys.tasks, "running"],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/tasks", {
					params: { query: { status: "running", limit: 100 } },
				}),
			),
		refetchInterval: 10_000,
	});
	const recent = useQuery({
		queryKey: [...keys.tasks, "recent"],
		queryFn: async () =>
			unwrap(await api.GET("/v1/tasks", { params: { query: { limit: 6 } } })),
		refetchInterval: 15_000,
	});
	const waiting = useOpenEscalations();
	const week = useQuery({
		queryKey: ["costs", "day", 7],
		enabled: operator,
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/costs", {
					params: { query: { group_by: "day", days: 7 } },
				}),
			),
	});

	const runningItems = running.data?.items ?? [];
	const waitingItems = waiting.data ?? [];
	const first = identity?.name.split(/\s+/)[0] ?? "";
	const firstName = first.charAt(0).toUpperCase() + first.slice(1);
	const count = (n: number | undefined, more?: boolean) =>
		n === undefined ? "–" : `${n}${more ? "+" : ""}`;

	return (
		<div className="relative">
			<div
				aria-hidden
				className="pointer-events-none absolute -top-24 left-1/2 -z-10 h-72 w-full max-w-5xl -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,var(--glow),transparent)] opacity-90"
			/>
			<section className="mx-auto max-w-3xl pt-4 text-center sm:pt-10">
				<motion.div
					initial={{ opacity: 0, scale: 0.9 }}
					animate={{ opacity: 1, scale: 1 }}
					transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
					className="mx-auto mb-5 grid size-12 place-items-center rounded-2xl bg-gradient-to-br from-[#5ac8fa] via-[#0a84ff] to-[#5e5ce6] text-white shadow-pop"
				>
					<Sparkles aria-hidden className="size-6" />
				</motion.div>
				<h1 className="text-[32px] leading-tight font-bold tracking-tight sm:text-[40px]">
					{greeting()}
					{firstName ? `, ${firstName}` : ""}
				</h1>
				<p className="mx-auto mt-2 max-w-xl text-[15px] text-muted sm:text-[17px]">
					Describe the outcome you want. Orchestrix plans it, runs it with
					specialist agents, and checks with you before anything risky.
				</p>
				{hasRole(identity, "requester") && (
					<div className="mt-8 text-left">
						<AskBox />
					</div>
				)}
			</section>

			<div
				className={cn(
					"mx-auto mt-10 grid max-w-5xl gap-3",
					operator ? "sm:grid-cols-3" : "sm:grid-cols-2",
				)}
			>
				<Stat
					icon={PlayCircle}
					label="Running"
					value={count(running.data?.items.length, !!running.data?.next_cursor)}
				/>
				<Stat
					icon={Hand}
					label="Needs your approval"
					value={count(waiting.data?.length)}
					tone={waitingItems.length > 0 ? "text-st-human" : undefined}
				/>
				{operator && (
					<Stat
						icon={CircleDollarSign}
						label="Spent in the last 7 days"
						value={week.data ? formatUsd(week.data.total_usd) : "–"}
					/>
				)}
			</div>

			<div className="mx-auto max-w-5xl">
				{(runningItems.length > 0 || waitingItems.length > 0) && (
					<>
						<SectionTitle
							to={waitingItems.length > 0 ? "/escalations" : "/tasks"}
						>
							In progress
						</SectionTitle>
						<ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
							{waitingItems.slice(0, 3).map((e, i) => (
								<WaitingCard key={e.id} item={e} index={i} />
							))}
							{runningItems
								.slice(0, Math.max(0, 6 - Math.min(3, waitingItems.length)))
								.map((t, i) => (
									<RunningCard
										key={t.id}
										task={t}
										index={i + Math.min(3, waitingItems.length)}
									/>
								))}
						</ul>
					</>
				)}

				<SectionTitle to="/tasks">Recent</SectionTitle>
				<ErrorNote error={recent.error} />
				{recent.isPending ? (
					<div className="panel h-48 animate-pulse" />
				) : (recent.data?.items ?? []).length === 0 ? (
					<div className="panel">
						<Empty icon={Sparkles}>
							No tasks yet. Describe one above to get started.
						</Empty>
					</div>
				) : (
					<TaskList tasks={recent.data?.items ?? []} />
				)}
			</div>
		</div>
	);
}
