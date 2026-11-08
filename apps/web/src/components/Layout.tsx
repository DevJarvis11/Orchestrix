import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import {
	Bell,
	Coins,
	Database,
	LayoutList,
	LogOut,
	Moon,
	Plus,
	Settings,
	Sun,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { api, unwrap } from "../lib/api/client";
import { hasRole, type Role, useAuth } from "../lib/auth";
import { keys } from "../lib/events";
import { Button, Empty } from "./ui";

function useTheme(): [boolean, () => void] {
	const [dark, setDark] = useState<boolean>(() => {
		try {
			const saved = localStorage.getItem("orchestrix-theme"); // a per-viewer convenience only
			if (saved) return saved === "dark";
		} catch {
			// storage unavailable: follow the OS
		}
		return window.matchMedia("(prefers-color-scheme: dark)").matches;
	});
	useEffect(() => {
		document.documentElement.classList.toggle("dark", dark);
		try {
			localStorage.setItem("orchestrix-theme", dark ? "dark" : "light");
		} catch {
			// ignore
		}
	}, [dark]);
	return [dark, () => setDark((d) => !d)];
}

const linkCls =
	"flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-fg [&.active]:bg-surface-2 [&.active]:font-semibold [&.active]:text-fg";

export function Layout() {
	const { identity, logout } = useAuth();
	const [dark, toggle] = useTheme();
	const inbox = useQuery({
		queryKey: [...keys.escalations, "badge"],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/escalations", {
					params: { query: { status: "open" } },
				}),
			),
		refetchInterval: 30_000,
	});
	const open = inbox.data?.length ?? 0;
	return (
		<div className="min-h-screen">
			<a
				href="#main"
				className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-surface focus:p-2"
			>
				Skip to content
			</a>
			<header className="sticky top-0 z-30 border-b border-border bg-surface/95 backdrop-blur">
				<div className="mx-auto flex max-w-screen-2xl items-center gap-4 px-4 py-2">
					<Link to="/" className="font-semibold tracking-tight">
						Orchestrix
					</Link>
					<nav
						aria-label="Main"
						className="flex flex-1 flex-wrap items-center gap-1"
					>
						<Link to="/" className={linkCls} activeOptions={{ exact: true }}>
							<LayoutList aria-hidden className="size-4" /> Tasks
						</Link>
						{hasRole(identity, "requester") && (
							<Link to="/tasks/new" className={linkCls}>
								<Plus aria-hidden className="size-4" /> New task
							</Link>
						)}
						<Link to="/escalations" className={linkCls}>
							<Bell aria-hidden className="size-4" /> Escalations
							{open > 0 && (
								<span className="rounded-full bg-st-human px-1.5 text-xs font-semibold text-white">
									{open}
									<span className="sr-only"> open</span>
								</span>
							)}
						</Link>
						<Link to="/memory" className={linkCls}>
							<Database aria-hidden className="size-4" /> Memory
						</Link>
						{hasRole(identity, "operator") && (
							<Link to="/costs" className={linkCls}>
								<Coins aria-hidden className="size-4" /> Costs
							</Link>
						)}
						{hasRole(identity, "operator") && (
							<Link to="/admin" className={linkCls}>
								<Settings aria-hidden className="size-4" /> Admin
							</Link>
						)}
					</nav>
					<span className="hidden text-sm text-muted lg:inline">
						{identity?.name} · {identity?.roles.join(", ")}
					</span>
					<Button
						variant="ghost"
						size="sm"
						onClick={toggle}
						aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
					>
						{dark ? (
							<Sun aria-hidden className="size-4" />
						) : (
							<Moon aria-hidden className="size-4" />
						)}
					</Button>
					<Button
						variant="ghost"
						size="sm"
						onClick={() => void logout()}
						aria-label="Sign out"
					>
						<LogOut aria-hidden className="size-4" />
					</Button>
				</div>
			</header>
			<main id="main" className="mx-auto max-w-screen-2xl px-4 py-5">
				<Outlet />
			</main>
		</div>
	);
}

/** Route guard from the token's roles (SPEC 18.2). The API enforces the same rules. */
export function RequireRole({
	roles,
	children,
}: {
	roles: Role[];
	children: ReactNode;
}) {
	const { identity } = useAuth();
	if (!hasRole(identity, ...roles)) {
		return <Empty>This page needs the {roles.join(" or ")} role.</Empty>;
	}
	return <>{children}</>;
}

export function PageHeader({
	title,
	children,
}: {
	title: string;
	children?: ReactNode;
}) {
	return (
		<div className="mb-4 flex flex-wrap items-center justify-between gap-3">
			<h1 className="text-xl font-semibold tracking-tight">{title}</h1>
			<div className="flex items-center gap-2">{children}</div>
		</div>
	);
}
