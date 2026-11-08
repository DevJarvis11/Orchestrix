// Admin (SPEC 18.1, FR-91, FR-92, A4): tools registry, budgets, model defaults, audit log + CSV.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "../components/Layout";
import {
	Button,
	Card,
	ErrorNote,
	Input,
	Select,
	Spinner,
	Tabs,
} from "../components/ui";
import { api, authedFetch, type Schemas, unwrap } from "../lib/api/client";
import { hasRole, useAuth } from "../lib/auth";

type Tab = "tools" | "budgets" | "models" | "audit";
const SPECIALISTS = ["research", "data_analyst", "writer", "coder"] as const;

export function Admin() {
	const { identity } = useAuth();
	const admin = hasRole(identity, "admin");
	const [tab, setTab] = useState<Tab>("tools");
	const options: { value: Tab; label: string }[] = [
		{ value: "tools", label: "Tools" },
		{ value: "budgets", label: "Budgets" },
		...(admin
			? ([
					{ value: "models", label: "Models" },
					{ value: "audit", label: "Audit log" },
				] as const)
			: []),
	];
	return (
		<>
			<PageHeader title="Admin">
				<Tabs
					label="Admin section"
					value={tab}
					onChange={setTab}
					options={options}
				/>
			</PageHeader>
			{tab === "tools" && <Tools />}
			{tab === "budgets" && <BudgetsPanel />}
			{tab === "models" && admin && <ModelDefaults />}
			{tab === "audit" && admin && <Audit />}
		</>
	);
}

function Tools() {
	const qc = useQueryClient();
	const tools = useQuery({
		queryKey: ["admin-tools"],
		queryFn: async () => unwrap(await api.GET("/v1/admin/tools")),
	});
	const patch = useMutation({
		mutationFn: async ({
			name,
			body,
		}: {
			name: string;
			body: Schemas["ToolPatch"];
		}) =>
			unwrap(
				await api.PATCH("/v1/admin/tools/{name}", {
					params: { path: { name } },
					body,
				}),
			),
		onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin-tools"] }),
	});
	if (tools.isPending) return <Spinner />;
	return (
		<Card title="Tool registry">
			<ErrorNote error={tools.error ?? patch.error} />
			<div className="overflow-x-auto">
				<table className="w-full text-sm">
					<caption className="sr-only">Tools</caption>
					<thead className="text-left text-muted">
						<tr>
							<th scope="col" className="py-1 pr-3 font-medium">
								Tool
							</th>
							<th scope="col" className="py-1 pr-3 font-medium">
								Enabled
							</th>
							<th scope="col" className="py-1 pr-3 font-medium">
								Sensitive
							</th>
							<th scope="col" className="py-1 pr-3 font-medium">
								Rate / min
							</th>
							<th scope="col" className="py-1 font-medium">
								Allowed specialists
							</th>
						</tr>
					</thead>
					<tbody>
						{tools.data?.map((t) => (
							<tr key={t.name} className="border-t border-border">
								<th
									scope="row"
									className="py-2 pr-3 text-left font-mono text-xs font-normal"
								>
									{t.name}
								</th>
								<td className="py-2 pr-3">
									<input
										type="checkbox"
										className="size-4"
										aria-label={`${t.name} enabled`}
										checked={t.enabled}
										onChange={(e) =>
											patch.mutate({
												name: t.name,
												body: { enabled: e.target.checked },
											})
										}
									/>
								</td>
								<td className="py-2 pr-3">
									<input
										type="checkbox"
										className="size-4"
										aria-label={`${t.name} sensitive`}
										checked={t.sensitive}
										onChange={(e) =>
											patch.mutate({
												name: t.name,
												body: { sensitive: e.target.checked },
											})
										}
									/>
								</td>
								<td className="py-2 pr-3">
									<Input
										type="number"
										min={1}
										aria-label={`${t.name} rate limit per minute`}
										defaultValue={t.rate_limit_per_min}
										className="w-24"
										onBlur={(e) => {
											const v = Number(e.target.value);
											if (v >= 1 && v !== t.rate_limit_per_min)
												patch.mutate({
													name: t.name,
													body: { rate_limit_per_min: v },
												});
										}}
									/>
								</td>
								<td className="py-2">
									<fieldset className="flex flex-wrap gap-3">
										<legend className="sr-only">
											{t.name} allowed specialists
										</legend>
										{SPECIALISTS.map((s) => (
											<label key={s} className="flex items-center gap-1">
												<input
													type="checkbox"
													className="size-4"
													checked={t.allowed_specialists.includes(s)}
													onChange={(e) => {
														const next = e.target.checked
															? [...t.allowed_specialists, s]
															: t.allowed_specialists.filter((x) => x !== s);
														patch.mutate({
															name: t.name,
															body: {
																allowed_specialists: next.filter(
																	(x): x is (typeof SPECIALISTS)[number] =>
																		(SPECIALISTS as readonly string[]).includes(
																			x,
																		),
																),
															},
														});
													}}
												/>
												{s}
											</label>
										))}
									</fieldset>
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>
		</Card>
	);
}

const BUDGET_FIELDS = [
	"max_tokens",
	"max_usd",
	"max_seconds",
	"max_tool_calls",
	"max_steps",
] as const;

function BudgetsPanel() {
	const qc = useQueryClient();
	const budgets = useQuery({
		queryKey: ["admin-budgets"],
		queryFn: async () => unwrap(await api.GET("/v1/admin/budgets")),
	});
	const [draft, setDraft] = useState<Record<string, string>>({});
	const [teams, setTeams] = useState<Record<string, string>>({});
	const save = useMutation({
		mutationFn: async () => {
			const current = budgets.data?.default_run_budget;
			if (!current) return;
			const merged = { ...current };
			for (const f of BUDGET_FIELDS)
				if (draft[f] !== undefined && draft[f] !== "")
					merged[f] = Number(draft[f]);
			const teamMap: Record<string, number> = {};
			for (const [id, v] of Object.entries(teams))
				if (v !== "") teamMap[id] = Number(v);
			unwrap(
				await api.PUT("/v1/admin/budgets", {
					body: { default_run_budget: merged, teams: teamMap },
				}),
			);
		},
		onSuccess: () => {
			setDraft({});
			setTeams({});
			void qc.invalidateQueries({ queryKey: ["admin-budgets"] });
		},
	});
	if (budgets.isPending) return <Spinner />;
	const b = budgets.data;
	return (
		<div className="grid gap-4 lg:grid-cols-2">
			<Card title="Default run budget (FR-63)">
				<div className="grid grid-cols-2 gap-3">
					{BUDGET_FIELDS.map((f) => (
						<label key={f} className="text-sm" htmlFor={`budget-${f}`}>
							<span className="mb-1 block text-muted">
								{f.replace(/_/g, " ")}
							</span>
							<Input
								id={`budget-${f}`}
								type="number"
								step="any"
								min={0}
								value={draft[f] ?? String(b?.default_run_budget[f] ?? "")}
								onChange={(e) =>
									setDraft((d) => ({ ...d, [f]: e.target.value }))
								}
							/>
						</label>
					))}
				</div>
			</Card>
			<Card title="Team monthly budgets (USD)">
				<ul className="space-y-2">
					{b?.teams.map((t) => (
						<li
							key={t.team_id}
							className="flex items-center justify-between gap-3 text-sm"
						>
							<span>{t.name}</span>
							<Input
								type="number"
								min={0}
								step="any"
								className="w-32"
								aria-label={`${t.name} monthly budget`}
								value={teams[t.team_id] ?? String(t.monthly_budget_usd)}
								onChange={(e) =>
									setTeams((x) => ({ ...x, [t.team_id]: e.target.value }))
								}
							/>
						</li>
					))}
				</ul>
			</Card>
			<div className="lg:col-span-2">
				<ErrorNote error={budgets.error ?? save.error} />
				<Button
					variant="primary"
					busy={save.isPending}
					onClick={() => save.mutate()}
				>
					Save budgets
				</Button>
			</div>
		</div>
	);
}

function ModelDefaults() {
	const qc = useQueryClient();
	const defaults = useQuery({
		queryKey: ["admin-models"],
		queryFn: async () => unwrap(await api.GET("/v1/admin/model-defaults")),
	});
	const models = useQuery({
		queryKey: ["models"],
		queryFn: async () => unwrap(await api.GET("/v1/models")),
	});
	const put = useMutation({
		mutationFn: async ({
			role,
			model,
		}: {
			role: Schemas["ModelDefaultView"]["role"];
			model: string;
		}) =>
			unwrap(
				await api.PUT("/v1/admin/model-defaults/{role}", {
					params: { path: { role } },
					body: { model, params: {} },
				}),
			),
		onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin-models"] }),
	});
	if (defaults.isPending) return <Spinner />;
	return (
		<Card title="Default model per role (amendment A4)">
			<ErrorNote error={defaults.error ?? models.error ?? put.error} />
			<ul className="space-y-3">
				{defaults.data?.map((d) => (
					<li
						key={d.role}
						className="flex flex-wrap items-center gap-3 text-sm"
					>
						<span className="w-24 font-medium">{d.role}</span>
						<Select
							aria-label={`${d.role} model`}
							value={d.model}
							className="w-64"
							onChange={(e) =>
								put.mutate({ role: d.role, model: e.target.value })
							}
						>
							{[
								...new Set([
									d.model,
									...(models.data?.models.map((m) => m.id) ?? []),
								]),
							].map((m) => (
								<option key={m} value={m}>
									{m}
								</option>
							))}
						</Select>
						<span className="text-xs text-muted">
							{d.source === "admin" ? "set by an admin" : "from settings"}
						</span>
					</li>
				))}
			</ul>
		</Card>
	);
}

function Audit() {
	const [action, setAction] = useState("");
	const log = useQuery({
		queryKey: ["audit", action],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/admin/audit", {
					params: {
						query: { format: "json", limit: 200, action: action || null },
					},
				}),
			),
	});
	const exportCsv = useMutation({
		mutationFn: async () => {
			const r = await authedFetch(
				`/v1/admin/audit?format=csv&limit=10000${action ? `&action=${encodeURIComponent(action)}` : ""}`,
			);
			if (!r.ok) throw new Error(`export failed: HTTP ${r.status}`);
			const url = URL.createObjectURL(await r.blob());
			const a = document.createElement("a");
			a.href = url;
			a.download = "audit.csv";
			a.click();
			URL.revokeObjectURL(url);
		},
	});
	const rows = (log.data ?? []) as Schemas["AuditEntry"][];
	return (
		<Card
			title="Audit log"
			actions={
				<Button
					size="sm"
					onClick={() => exportCsv.mutate()}
					busy={exportCsv.isPending}
				>
					<Download aria-hidden className="size-4" /> Export CSV
				</Button>
			}
		>
			<label
				className="mb-3 flex items-center gap-2 text-sm"
				htmlFor="audit-action"
			>
				<span className="text-muted">Action</span>
				<Input
					id="audit-action"
					value={action}
					onChange={(e) => setAction(e.target.value)}
					placeholder="e.g. escalation.approve"
					className="w-64"
				/>
			</label>
			<ErrorNote error={log.error ?? exportCsv.error} />
			{log.isPending ? (
				<Spinner />
			) : (
				<div className="overflow-x-auto">
					<table className="w-full text-xs">
						<caption className="sr-only">Audit log</caption>
						<thead className="text-left text-muted">
							<tr>
								<th scope="col" className="py-1 pr-3 font-medium">
									When
								</th>
								<th scope="col" className="py-1 pr-3 font-medium">
									Actor
								</th>
								<th scope="col" className="py-1 pr-3 font-medium">
									Action
								</th>
								<th scope="col" className="py-1 pr-3 font-medium">
									Target
								</th>
								<th scope="col" className="py-1 font-medium">
									Reason
								</th>
							</tr>
						</thead>
						<tbody>
							{rows.map((r) => (
								<tr key={r.id} className="border-t border-border align-top">
									<td className="py-1 pr-3 whitespace-nowrap">
										{new Date(r.created_at).toLocaleString()}
									</td>
									<td className="py-1 pr-3 font-mono">
										{r.actor_id ? r.actor_id.slice(0, 8) : "system"}
									</td>
									<td className="py-1 pr-3">{r.action}</td>
									<td className="py-1 pr-3">
										{r.target_type}:{r.target_id.slice(0, 24)}
									</td>
									<td className="py-1">{r.reason ?? ""}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
		</Card>
	);
}
