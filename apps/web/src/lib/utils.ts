import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
	return twMerge(clsx(inputs));
}

export function formatUsd(value: number): string {
	if (value === 0) return "$0";
	if (value < 0.01) return `$${value.toFixed(4)}`;
	return `$${value.toFixed(2)}`;
}

export function formatTokens(value: number): string {
	if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
	if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
	return String(value);
}

export function relativeTime(iso: string, now: number = Date.now()): string {
	const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
	const abs = Math.abs(seconds);
	const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
	if (abs < 60) return rtf.format(seconds, "second");
	if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
	if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
	return rtf.format(Math.round(seconds / 86400), "day");
}

export function shortId(id: string): string {
	return id.slice(0, 8);
}

/** A task title from a free-text request: its first line, at most `max` characters. */
export function titleFromPrompt(text: string, max = 80): string {
	const line =
		text
			.split("\n")
			.map((l) => l.replace(/\s+/g, " ").trim())
			.find(Boolean) ?? "";
	if (line.length <= max) return line;
	const cut = line.slice(0, max - 1);
	const space = cut.lastIndexOf(" ");
	return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

export function greeting(hour: number = new Date().getHours()): string {
	if (hour < 5) return "Good evening";
	if (hour < 12) return "Good morning";
	if (hour < 18) return "Good afternoon";
	return "Good evening";
}
