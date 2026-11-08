// Small accessible primitives in an Apple-like style (hand-written; no CLI).
import { AlertCircle, ChevronDown, Inbox, Loader2, X } from "lucide-react";
import { motion } from "motion/react";
import {
	type ButtonHTMLAttributes,
	forwardRef,
	type InputHTMLAttributes,
	type ReactNode,
	type SelectHTMLAttributes,
	type TextareaHTMLAttributes,
	useEffect,
	useId,
	useRef,
} from "react";
import { cn } from "../../lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string> = {
	primary: "bg-accent text-accent-fg shadow-sm hover:brightness-110",
	secondary: "bg-fill text-fg hover:bg-fill/80",
	ghost: "text-fg hover:bg-fill",
	danger: "bg-danger/10 text-danger hover:bg-danger/15",
};

/** Button look for links that act as buttons (router <Link>, <a download>). */
export function buttonClass(
	variant: Variant = "secondary",
	size: "sm" | "md" = "md",
): string {
	return cn(
		"inline-flex items-center justify-center gap-1.5 rounded-full font-medium whitespace-nowrap transition duration-150 select-none active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100",
		size === "sm" ? "h-8 px-3 text-[13px]" : "h-9 px-4 text-sm",
		VARIANTS[variant],
	);
}

export const Button = forwardRef<
	HTMLButtonElement,
	ButtonHTMLAttributes<HTMLButtonElement> & {
		variant?: Variant;
		size?: "sm" | "md";
		busy?: boolean;
	}
>(function Button(
	{
		variant = "secondary",
		size = "md",
		busy,
		className,
		children,
		disabled,
		...rest
	},
	ref,
) {
	return (
		<button
			ref={ref}
			type="button"
			disabled={disabled || busy}
			aria-busy={busy || undefined}
			className={cn(buttonClass(variant, size), className)}
			{...rest}
		>
			{busy && <Loader2 aria-hidden className="size-4 animate-spin" />}
			{children}
		</button>
	);
});

export function Card({
	title,
	actions,
	children,
	className,
}: {
	title?: ReactNode;
	actions?: ReactNode;
	children: ReactNode;
	className?: string;
}) {
	return (
		<section className={cn("panel", className)}>
			{(title || actions) && (
				<header className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4">
					{title && (
						<h2 className="text-[15px] font-semibold tracking-tight">
							{title}
						</h2>
					)}
					{actions}
				</header>
			)}
			<div className="p-5">{children}</div>
		</section>
	);
}

const field =
	"w-full rounded-xl border border-transparent bg-fill px-3 py-2 text-sm text-fg placeholder:text-faint transition focus:border-accent focus:bg-surface focus:outline-none focus:ring-4 focus:ring-accent/15 disabled:opacity-60 aria-[invalid=true]:border-danger";

export const Input = forwardRef<
	HTMLInputElement,
	InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...rest }, ref) {
	return <input ref={ref} className={cn(field, "h-9", className)} {...rest} />;
});

export const Textarea = forwardRef<
	HTMLTextAreaElement,
	TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...rest }, ref) {
	return (
		<textarea
			ref={ref}
			className={cn(field, "min-h-24 leading-relaxed", className)}
			{...rest}
		/>
	);
});

/** Native <select> with a quiet chevron. `className` sizes the wrapper. */
export const Select = forwardRef<
	HTMLSelectElement,
	SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, ...rest }, ref) {
	return (
		<div className={cn("relative", className)}>
			<select
				ref={ref}
				className={cn(field, "h-9 cursor-pointer appearance-none pr-9")}
				{...rest}
			/>
			<ChevronDown
				aria-hidden
				className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted"
			/>
		</div>
	);
});

export function Field({
	label,
	error,
	hint,
	children,
}: {
	label: string;
	error?: string;
	hint?: string;
	children: (id: string) => ReactNode;
}) {
	const id = useId();
	return (
		<div className="space-y-1.5">
			<label
				htmlFor={id}
				className="block text-[13px] font-medium text-fg first-letter:uppercase"
			>
				{label}
			</label>
			{children(id)}
			{hint && !error && <p className="text-xs text-muted">{hint}</p>}
			{error && (
				<p role="alert" className="text-xs text-danger">
					{error}
				</p>
			)}
		</div>
	);
}

export function Spinner({ label = "Loading" }: { label?: string }) {
	return (
		<div
			role="status"
			className="flex items-center justify-center gap-2 p-10 text-sm text-muted"
		>
			<Loader2 aria-hidden className="size-4 animate-spin" />
			{label}…
		</div>
	);
}

export function ErrorNote({ error }: { error: unknown }) {
	if (!error) return null;
	return (
		<p
			role="alert"
			className="mb-3 flex items-start gap-2 rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm text-danger"
		>
			<AlertCircle aria-hidden className="mt-0.5 size-4 shrink-0" />
			{error instanceof Error ? error.message : String(error)}
		</p>
	);
}

export function Empty({
	children,
	icon: Icon = Inbox,
}: {
	children: ReactNode;
	icon?: typeof Inbox;
}) {
	return (
		<div className="flex flex-col items-center gap-3 px-6 py-12 text-center text-sm text-muted">
			<span className="grid size-12 place-items-center rounded-full bg-fill">
				<Icon aria-hidden className="size-5 text-faint" />
			</span>
			<p>{children}</p>
		</div>
	);
}

/** Native <dialog>: focus trap, Escape and inert background come from the browser. */
export function Modal({
	open,
	onClose,
	title,
	children,
	side = false,
}: {
	open: boolean;
	onClose: () => void;
	title: string;
	children: ReactNode;
	side?: boolean;
}) {
	const ref = useRef<HTMLDialogElement>(null);
	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		if (open && !el.open) el.showModal();
		if (!open && el.open) el.close();
	}, [open]);
	return (
		<dialog
			ref={ref}
			onClose={onClose}
			aria-label={title}
			className={cn(
				"bg-surface text-fg shadow-pop",
				side
					? "mr-0 ml-auto h-full max-h-none w-[min(42rem,100vw)] max-w-none rounded-l-2xl"
					: "m-auto w-[min(36rem,95vw)] rounded-2xl",
			)}
		>
			{open && (
				<motion.div
					initial={{ opacity: 0, x: side ? 24 : 0, scale: side ? 1 : 0.97 }}
					animate={{ opacity: 1, x: 0, scale: 1 }}
					transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
					className="flex h-full flex-col"
				>
					<header className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
						<h2 className="text-[17px] font-semibold tracking-tight">
							{title}
						</h2>
						<Button
							variant="secondary"
							size="sm"
							aria-label="Close"
							onClick={onClose}
							className="size-8 px-0"
						>
							<X aria-hidden className="size-4" />
						</Button>
					</header>
					<div className="flex-1 overflow-auto p-5">{children}</div>
				</motion.div>
			)}
		</dialog>
	);
}

/** Segmented control (iOS style) with a sliding selection and arrow-key support. */
export function Tabs<T extends string>({
	value,
	onChange,
	options,
	label,
}: {
	value: T;
	onChange: (v: T) => void;
	options: { value: T; label: string }[];
	label: string;
}) {
	const group = useId();
	return (
		<div
			role="tablist"
			aria-label={label}
			className="inline-flex rounded-[10px] bg-fill p-0.5"
		>
			{options.map((o) => (
				<button
					key={o.value}
					type="button"
					role="tab"
					aria-selected={value === o.value}
					tabIndex={value === o.value ? 0 : -1}
					onClick={() => onChange(o.value)}
					onKeyDown={(e) => {
						const i = options.findIndex((x) => x.value === value);
						const next =
							e.key === "ArrowRight"
								? options[(i + 1) % options.length]
								: e.key === "ArrowLeft"
									? options[(i - 1 + options.length) % options.length]
									: undefined;
						if (next) {
							e.preventDefault();
							onChange(next.value);
						}
					}}
					className={cn(
						"relative rounded-lg px-3.5 py-1.5 text-[13px] font-medium transition-colors",
						value === o.value ? "text-fg" : "text-muted hover:text-fg",
					)}
				>
					{value === o.value && (
						<motion.span
							layoutId={`seg-${group}`}
							transition={{ type: "spring", stiffness: 500, damping: 38 }}
							className="absolute inset-0 rounded-lg bg-surface shadow-[0_1px_3px_rgb(0_0_0/0.15)]"
						/>
					)}
					<span className="relative">{o.label}</span>
				</button>
			))}
		</div>
	);
}

/** JSON text editor with live validation; `onValid` receives the parsed value. */
export function JsonEditor({
	value,
	onChange,
	label,
	rows = 14,
}: {
	value: string;
	onChange: (text: string) => void;
	label: string;
	rows?: number;
}) {
	let error: string | null = null;
	try {
		JSON.parse(value);
	} catch (e) {
		error = e instanceof Error ? e.message : "invalid JSON";
	}
	return (
		<Field label={label} error={error ?? undefined}>
			{(id) => (
				<Textarea
					id={id}
					rows={rows}
					spellCheck={false}
					aria-invalid={error !== null}
					className="font-mono text-xs"
					value={value}
					onChange={(e) => onChange(e.target.value)}
				/>
			)}
		</Field>
	);
}

export function Kv({ items }: { items: [string, ReactNode][] }) {
	return (
		<dl className="divide-y divide-border text-sm">
			{items.map(([k, v]) => (
				<div key={k} className="flex items-baseline justify-between gap-4 py-2">
					<dt className="shrink-0 text-muted">{k}</dt>
					<dd className="min-w-0 text-right break-words">{v}</dd>
				</div>
			))}
		</dl>
	);
}

/** A big number with a label, for summary rows. */
export function Stat({
	label,
	value,
	tone,
	icon: Icon,
}: {
	label: string;
	value: ReactNode;
	tone?: string;
	icon?: typeof Inbox;
}) {
	return (
		<div className="panel px-5 py-4">
			<div className="flex items-center gap-1.5 text-[13px] text-muted">
				{Icon && <Icon aria-hidden className="size-4" />}
				{label}
			</div>
			<div
				className={cn(
					"mt-1 text-[28px] leading-tight font-semibold tracking-tight tabular-nums",
					tone,
				)}
			>
				{value}
			</div>
		</div>
	);
}
