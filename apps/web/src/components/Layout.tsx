import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
	Bell,
	Coins,
	Database,
	House,
	LayoutList,
	LogOut,
	type LucideIcon,
	Menu,
	Monitor,
	Moon,
	Plus,
	Settings2,
	Sun,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useEffect, useId, useState } from "react";
import { api, unwrap } from "../lib/api/client";
import { hasRole, type Identity, type Role, useAuth } from "../lib/auth";
import { keys } from "../lib/events";
import { type ThemeMode, useTheme } from "../lib/theme";
import { cn } from "../lib/utils";
import { Logo } from "./Logo";
import { Button, buttonClass, Empty } from "./ui";

/** Open escalations, shared by the sidebar badge and the Home summary. */
export function useOpenEscalations() {
	return useQuery({
		queryKey: [...keys.escalations, "open"],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/escalations", {
					params: { query: { status: "open" } },
				}),
			),
		refetchInterval: 30_000,
	});
}

const THEMES: { mode: ThemeMode; label: string; icon: LucideIcon }[] = [
	{ mode: "light", label: "Light", icon: Sun },
	{ mode: "dark", label: "Dark", icon: Moon },
	{ mode: "system", label: "Match system", icon: Monitor },
];

function ThemeSwitch({
	mode,
	setMode,
}: {
	mode: ThemeMode;
	setMode: (m: ThemeMode) => void;
}) {
	const pill = useId();
	return (
		<fieldset className="flex rounded-[10px] bg-fill p-0.5">
			<legend className="sr-only">Theme</legend>
			{THEMES.map(({ mode: m, label, icon: Icon }) => (
				<button
					key={m}
					type="button"
					aria-pressed={mode === m}
					aria-label={label}
					title={label}
					onClick={() => setMode(m)}
					className={cn(
						"relative grid h-7 flex-1 place-items-center rounded-lg transition-colors",
						mode === m ? "text-fg" : "text-muted hover:text-fg",
					)}
				>
					{mode === m && (
						<motion.span
							layoutId={pill}
							transition={{ type: "spring", stiffness: 500, damping: 38 }}
							className="absolute inset-0 rounded-lg bg-surface shadow-[0_1px_3px_rgb(0_0_0/0.15)]"
						/>
					)}
					<Icon aria-hidden className="relative size-3.5" />
				</button>
			))}
		</fieldset>
	);
}

function NavItem({
	to,
	icon: Icon,
	label,
	exact,
	badge,
}: {
	to: "/" | "/tasks" | "/escalations" | "/memory" | "/costs" | "/admin";
	icon: LucideIcon;
	label: string;
	exact?: boolean;
	badge?: number;
}) {
	return (
		<Link
			to={to}
			activeOptions={{ exact: exact ?? false }}
			className="group flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-sm text-fg/90 transition-colors hover:bg-fill [&.active]:bg-accent/12 [&.active]:font-medium [&.active]:text-accent"
		>
			<Icon
				aria-hidden
				className="size-[18px] text-accent transition-transform duration-200 group-hover:scale-110"
			/>
			{label}
			{badge !== undefined && badge > 0 && (
				<motion.span
					initial={{ scale: 0.6, opacity: 0 }}
					animate={{ scale: 1, opacity: 1 }}
					className="ml-auto min-w-5 rounded-full bg-st-human px-1.5 text-center text-[11px] leading-5 font-semibold text-white"
				>
					{badge}
					<span className="sr-only"> open</span>
				</motion.span>
			)}
		</Link>
	);
}

function initials(identity: Identity | null): string {
	const name = identity?.name || identity?.email || "?";
	return name
		.split(/[\s@._-]+/)
		.filter(Boolean)
		.slice(0, 2)
		.map((p) => p[0]?.toUpperCase())
		.join("");
}

function Sidebar({ theme }: { theme: ReturnType<typeof useTheme> }) {
	const { identity, logout } = useAuth();
	const open = useOpenEscalations().data?.length ?? 0;
	const operator = hasRole(identity, "operator");
	return (
		<div className="flex h-full flex-col gap-1 px-3 pt-5 pb-4">
			<Link to="/" className="mb-5 rounded-lg px-2">
				<Logo />
			</Link>
			{hasRole(identity, "requester") && (
				<Link
					to="/tasks/new"
					className={cn(buttonClass("primary", "sm"), "mx-1 mb-4 h-9")}
				>
					<Plus aria-hidden className="size-4" /> New task
				</Link>
			)}
			<nav aria-label="Main" className="flex flex-col gap-0.5">
				<NavItem to="/" icon={House} label="Home" exact />
				<NavItem to="/tasks" icon={LayoutList} label="Tasks" />
				<NavItem to="/escalations" icon={Bell} label="Approvals" badge={open} />
				<NavItem to="/memory" icon={Database} label="Memory" />
				{operator && (
					<>
						<p className="mt-5 mb-1 px-2.5 text-[11px] font-semibold tracking-wide text-muted uppercase">
							Operations
						</p>
						<NavItem to="/costs" icon={Coins} label="Costs" />
						<NavItem to="/admin" icon={Settings2} label="Admin" />
					</>
				)}
			</nav>
			<div className="mt-auto space-y-3 pt-6">
				<ThemeSwitch mode={theme[0]} setMode={theme[1]} />
				<div className="flex items-center gap-2.5 rounded-xl px-1.5 py-1">
					<span
						aria-hidden
						className="grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[#5ac8fa] to-[#0a84ff] text-xs font-semibold text-white"
					>
						{initials(identity)}
					</span>
					<span className="min-w-0 flex-1">
						<span className="block truncate text-sm font-medium">
							{identity?.name}
						</span>
						<span className="block truncate text-xs text-muted capitalize">
							{identity?.roles.join(", ")}
						</span>
					</span>
					<Button
						variant="ghost"
						size="sm"
						onClick={() => void logout()}
						aria-label="Sign out"
						title="Sign out"
						className="size-8 px-0 text-muted"
					>
						<LogOut aria-hidden className="size-4" />
					</Button>
				</div>
			</div>
		</div>
	);
}

export function Layout() {
	const pathname = useRouterState({ select: (s) => s.location.pathname });
	const [menuOpen, setMenuOpen] = useState(false);
	const theme = useTheme();
	// biome-ignore lint/correctness/useExhaustiveDependencies: close the drawer on navigation
	useEffect(() => setMenuOpen(false), [pathname]);

	return (
		<div className="min-h-screen">
			<a
				href="#main"
				className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2 focus:shadow-pop"
			>
				Skip to content
			</a>

			{/* Desktop: a fixed frosted sidebar. */}
			<aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-border bg-sidebar backdrop-blur-2xl backdrop-saturate-150 lg:block">
				<Sidebar theme={theme} />
			</aside>

			{/* Mobile: a frosted top bar and a slide-in drawer. */}
			<header className="glass sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border px-4 lg:hidden">
				<Button
					variant="ghost"
					size="sm"
					aria-label="Open menu"
					aria-expanded={menuOpen}
					onClick={() => setMenuOpen(true)}
					className="size-9 px-0"
				>
					<Menu aria-hidden className="size-5" />
				</Button>
				<Link to="/">
					<Logo />
				</Link>
			</header>
			<AnimatePresence>
				{menuOpen && (
					<>
						<motion.div
							key="scrim"
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							exit={{ opacity: 0 }}
							onClick={() => setMenuOpen(false)}
							className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm lg:hidden"
						/>
						<motion.aside
							key="drawer"
							initial={{ x: "-100%" }}
							animate={{ x: 0 }}
							exit={{ x: "-100%" }}
							transition={{ type: "spring", stiffness: 420, damping: 40 }}
							onKeyDown={(e) => e.key === "Escape" && setMenuOpen(false)}
							className="fixed inset-y-0 left-0 z-50 w-72 border-r border-border bg-surface shadow-pop lg:hidden"
						>
							<Sidebar theme={theme} />
						</motion.aside>
					</>
				)}
			</AnimatePresence>

			<div className="lg:pl-64">
				<main
					id="main"
					className="relative mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-10 lg:py-10"
				>
					<motion.div
						key={pathname}
						initial={{ opacity: 0, y: 10 }}
						animate={{ opacity: 1, y: 0 }}
						transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
					>
						<Outlet />
					</motion.div>
				</main>
			</div>
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
	subtitle,
	children,
}: {
	title: string;
	subtitle?: ReactNode;
	children?: ReactNode;
}) {
	return (
		<div className="mb-7 flex flex-wrap items-end justify-between gap-4">
			<div className="min-w-0">
				<h1 className="text-[28px] leading-tight font-bold tracking-tight sm:text-[32px]">
					{title}
				</h1>
				{subtitle && <p className="mt-1 text-[15px] text-muted">{subtitle}</p>}
			</div>
			<div className="flex flex-wrap items-center gap-2">{children}</div>
		</div>
	);
}
