// Small accessible primitives in the shadcn/ui style (hand-written; no CLI).
import { Loader2, X } from "lucide-react";
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
	primary: "bg-accent text-accent-fg hover:opacity-90",
	secondary: "border border-border bg-surface hover:bg-surface-2",
	ghost: "hover:bg-surface-2",
	danger: "bg-danger text-white hover:opacity-90",
};

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
			className={cn(
				"inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
				size === "sm" ? "h-8 px-2.5 text-sm" : "h-9 px-3.5 text-sm",
				VARIANTS[variant],
				className,
			)}
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
		<section
			className={cn("rounded-lg border border-border bg-surface", className)}
		>
			{(title || actions) && (
				<header className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
					{title && <h2 className="text-sm font-semibold">{title}</h2>}
					{actions}
				</header>
			)}
			<div className="p-4">{children}</div>
		</section>
	);
}

const field =
	"w-full rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm placeholder:text-muted disabled:opacity-60";

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
			className={cn(field, "min-h-24", className)}
			{...rest}
		/>
	);
});

export const Select = forwardRef<
	HTMLSelectElement,
	SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, ...rest }, ref) {
	return <select ref={ref} className={cn(field, "h-9", className)} {...rest} />;
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
		<div className="space-y-1">
			<label htmlFor={id} className="block text-sm font-medium">
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
			className="flex items-center gap-2 p-4 text-sm text-muted"
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
			className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger"
		>
			{error instanceof Error ? error.message : String(error)}
		</p>
	);
}

export function Empty({ children }: { children: ReactNode }) {
	return <p className="p-6 text-center text-sm text-muted">{children}</p>;
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
				"bg-surface text-fg backdrop:bg-black/40",
				side
					? "ml-auto mr-0 h-full max-h-none w-[min(42rem,100vw)] max-w-none border-l border-border"
					: "m-auto w-[min(36rem,95vw)] rounded-lg border border-border",
			)}
		>
			{open && (
				<div className="flex h-full flex-col">
					<header className="flex items-center justify-between border-b border-border px-4 py-3">
						<h2 className="font-semibold">{title}</h2>
						<Button
							variant="ghost"
							size="sm"
							aria-label="Close"
							onClick={onClose}
						>
							<X aria-hidden className="size-4" />
						</Button>
					</header>
					<div className="flex-1 overflow-auto p-4">{children}</div>
				</div>
			)}
		</dialog>
	);
}

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
	return (
		<div
			role="tablist"
			aria-label={label}
			className="inline-flex rounded-md border border-border bg-surface p-0.5"
		>
			{options.map((o) => (
				<button
					key={o.value}
					type="button"
					role="tab"
					aria-selected={value === o.value}
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
						"rounded px-3 py-1 text-sm",
						value === o.value
							? "bg-accent text-accent-fg"
							: "text-muted hover:text-fg",
					)}
				>
					{o.label}
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
		<dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
			{items.map(([k, v]) => (
				<div key={k} className="contents">
					<dt className="text-muted">{k}</dt>
					<dd className="min-w-0 break-words">{v}</dd>
				</div>
			))}
		</dl>
	);
}
