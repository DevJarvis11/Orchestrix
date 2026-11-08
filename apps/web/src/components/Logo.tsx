// The Orchestrix mark: three agents joined into one plan.
import { useId } from "react";
import { cn } from "../lib/utils";

export function LogoMark({ className }: { className?: string }) {
	// Unique per instance: a gradient id inside a hidden copy would blank the visible one.
	const gradient = `ox${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
	return (
		<svg
			viewBox="0 0 32 32"
			aria-hidden
			className={cn("size-7 shrink-0", className)}
		>
			<defs>
				<linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" stopColor="#5ac8fa" />
					<stop offset="0.55" stopColor="#0a84ff" />
					<stop offset="1" stopColor="#5e5ce6" />
				</linearGradient>
			</defs>
			<rect width="32" height="32" rx="8" fill={`url(#${gradient})`} />
			<path
				d="M10 21.5 16 10l6 11.5H10Z"
				fill="none"
				stroke="#fff"
				strokeOpacity="0.55"
				strokeWidth="1.6"
				strokeLinejoin="round"
			/>
			<circle cx="16" cy="10" r="3" fill="#fff" />
			<circle cx="10" cy="21.5" r="3" fill="#fff" />
			<circle cx="22" cy="21.5" r="3" fill="#fff" />
		</svg>
	);
}

export function Logo({ className }: { className?: string }) {
	return (
		<span className={cn("inline-flex items-center gap-2", className)}>
			<LogoMark />
			<span className="text-[15px] font-semibold tracking-tight">
				Orchestrix
			</span>
		</span>
	);
}
