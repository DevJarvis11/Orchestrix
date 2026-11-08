// Routes (SPEC 18.1), code-based TanStack Router.
import {
	createRootRoute,
	createRoute,
	createRouter,
	lazyRouteComponent,
} from "@tanstack/react-router";
import { Layout, RequireRole } from "./components/Layout";
import { Home } from "./routes/Home";
import { Inbox } from "./routes/Inbox";
import { MemoryPage } from "./routes/MemoryPage";
import { NewTask } from "./routes/NewTask";
import { TasksDashboard } from "./routes/TasksDashboard";

// Heavy screens (React Flow + elk, Recharts) load on demand.
const TaskDetail = lazyRouteComponent(
	() => import("./routes/TaskDetail"),
	"TaskDetail",
);
const TraceExplorer = lazyRouteComponent(
	() => import("./routes/TraceExplorer"),
	"TraceExplorer",
);
const Replay = lazyRouteComponent(() => import("./routes/Replay"), "Replay");
const Costs = lazyRouteComponent(() => import("./routes/Costs"), "Costs");
const DecisionView = lazyRouteComponent(
	() => import("./routes/DecisionView"),
	"DecisionView",
);
const Admin = lazyRouteComponent(() => import("./routes/Admin"), "Admin");

const root = createRootRoute({ component: Layout });

const home = createRoute({
	getParentRoute: () => root,
	path: "/",
	component: Home,
});
const tasks = createRoute({
	getParentRoute: () => root,
	path: "/tasks",
	component: TasksDashboard,
});
const newTask = createRoute({
	getParentRoute: () => root,
	path: "/tasks/new",
	// `q` carries text typed into the Home ask box.
	validateSearch: (search: Record<string, unknown>): { q?: string } =>
		typeof search.q === "string" ? { q: search.q } : {},
	component: () => (
		<RequireRole roles={["requester"]}>
			<NewTask />
		</RequireRole>
	),
});
const taskDetail = createRoute({
	getParentRoute: () => root,
	path: "/tasks/$taskId",
	component: TaskDetail,
});
const inbox = createRoute({
	getParentRoute: () => root,
	path: "/escalations",
	component: Inbox,
});
const decision = createRoute({
	getParentRoute: () => root,
	path: "/escalations/$id",
	component: DecisionView,
});
const trace = createRoute({
	getParentRoute: () => root,
	path: "/runs/$runId/trace",
	component: TraceExplorer,
});
const replay = createRoute({
	getParentRoute: () => root,
	path: "/runs/$runId/replay",
	component: () => (
		<RequireRole roles={["operator"]}>
			<Replay />
		</RequireRole>
	),
});
const memory = createRoute({
	getParentRoute: () => root,
	path: "/memory",
	component: MemoryPage,
});
const costs = createRoute({
	getParentRoute: () => root,
	path: "/costs",
	component: () => (
		<RequireRole roles={["operator"]}>
			<Costs />
		</RequireRole>
	),
});
const admin = createRoute({
	getParentRoute: () => root,
	path: "/admin",
	component: () => (
		<RequireRole roles={["operator"]}>
			<Admin />
		</RequireRole>
	),
});
const callback = createRoute({
	getParentRoute: () => root,
	path: "/auth/callback",
	component: Home,
});

const routeTree = root.addChildren([
	home,
	tasks,
	newTask,
	taskDetail,
	inbox,
	decision,
	trace,
	replay,
	memory,
	costs,
	admin,
	callback,
]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
	interface Register {
		router: typeof router;
	}
}
