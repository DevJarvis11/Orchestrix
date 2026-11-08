// Light / dark / follow-the-system theme. The choice is a per-browser convenience in localStorage;
// index.html applies it before first paint so there is no flash.
import { useEffect, useState } from "react";

export type ThemeMode = "light" | "dark" | "system";
export const THEME_KEY = "orchestrix-theme";

export function parseMode(saved: string | null): ThemeMode {
	return saved === "light" || saved === "dark" ? saved : "system";
}

export function resolveDark(mode: ThemeMode, systemDark: boolean): boolean {
	return mode === "dark" || (mode === "system" && systemDark);
}

function readMode(): ThemeMode {
	try {
		return parseMode(localStorage.getItem(THEME_KEY));
	} catch {
		return "system"; // storage unavailable: follow the OS
	}
}

export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
	const [mode, setMode] = useState<ThemeMode>(readMode);

	useEffect(() => {
		const media = window.matchMedia("(prefers-color-scheme: dark)");
		const root = document.documentElement;
		const apply = () =>
			root.classList.toggle("dark", resolveDark(mode, media.matches));
		root.classList.add("theme-fade");
		apply();
		const settle = window.setTimeout(
			() => root.classList.remove("theme-fade"),
			350,
		);
		media.addEventListener("change", apply);
		try {
			if (mode === "system") localStorage.removeItem(THEME_KEY);
			else localStorage.setItem(THEME_KEY, mode);
		} catch {
			// ignore: the choice just won't be remembered
		}
		return () => {
			window.clearTimeout(settle);
			media.removeEventListener("change", apply);
		};
	}, [mode]);

	return [mode, setMode];
}
