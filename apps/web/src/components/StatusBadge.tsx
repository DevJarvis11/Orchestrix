// Status shown as colour AND icon AND text (NFR-11, SPEC 18.2 palette).
import {
	Ban,
	CheckCircle2,
	CircleDashed,
	CircleDot,
	Clock,
	Eye,
	Hand,
	Loader2,
	type LucideIcon,
	SkipForward,
	XCircle,
} from "lucide-react";
import { cn } from "../lib/utils";

type Tone =
	| "pending"
	| "running"
	| "review"
	| "human"
	| "approved"
	| "failed"
	| "skipped";

const TONES: Record<Tone, { cls: string; icon: LucideIcon }> = {
	pending: {
		cls: "text-st-pending bg-st-pending/12",
		icon: CircleDashed,
	},
	running: {
		cls: "text-st-running bg-st-running/12",
		icon: Loader2,
	},
	review: {
		cls: "text-st-review bg-st-review/12",
		icon: Eye,
	},
	human: { cls: "text-st-human bg-st-human/12", icon: Hand },
	approved: {
		cls: "text-st-approved bg-st-approved/12",
		icon: CheckCircle2,
	},
	failed: {
		cls: "text-st-failed bg-st-failed/12",
		icon: XCircle,
	},
	skipped: {
		cls: "text-st-skipped border border-dashed border-st-skipped/50",
		icon: SkipForward,
	},
};

const STATUS_TONE: Record<string, Tone> = {
	queued: "pending",
	pending: "pending",
	planning: "running",
	running: "running",
	in_review: "review",
	reviewing: "review",
	awaiting_plan_approval: "human",
	awaiting_human: "human",
	paused: "human",
	open: "human",
	approved: "approved",
	completed: "approved",
	resolved: "approved",
	ok: "approved",
	approve: "approved",
	taken_over: "approved",
	failed: "failed",
	error: "failed",
	denied: "failed",
	reject: "failed",
	budget_exceeded: "failed",
	expired: "failed",
	cancelled: "skipped",
	skipped: "skipped",
	revise: "review",
	fallback: "human",
	escalate: "human",
};

export function toneOf(status: string | null | undefined): Tone {
	return STATUS_TONE[status ?? ""] ?? "pending";
}

export function StatusBadge({
	status,
	className,
}: {
	status: string | null | undefined;
	className?: string;
}) {
	const tone = toneOf(status);
	const { cls, icon } = TONES[tone];
	const Icon =
		status === "cancelled"
			? Ban
			: status === "queued"
				? Clock
				: tone === "pending" && status
					? CircleDot
					: icon;
	const label = (status ?? "unknown").replace(/_/g, " ");
	return (
		<span
			className={cn(
				"inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
				cls,
				className,
			)}
		>
			<Icon
				aria-hidden
				className={cn(
					"size-3.5 shrink-0",
					tone === "running" && "animate-spin [animation-duration:2s]",
				)}
			/>
			<span className="inline-block first-letter:uppercase">{label}</span>
		</span>
	);
}
