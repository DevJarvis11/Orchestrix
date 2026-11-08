// Costs (SPEC 18.1, FR-62, A4): by day, agent, model, team and task type. Notional prices are labelled.
import { useQuery } from "@tanstack/react-query";
import { CircleDollarSign, PlayCircle, Wrench } from "lucide-react";
import { useState } from "react";
import {
	Bar,
	BarChart,
	CartesianGrid,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from "recharts";
import { PageHeader } from "../components/Layout";
import {
	Card,
	Empty,
	ErrorNote,
	Select,
	Spinner,
	Stat,
	Tabs,
} from "../components/ui";
import { api, unwrap } from "../lib/api/client";
import { formatTokens, formatUsd } from "../lib/utils";

type GroupBy = "day" | "agent" | "model" | "team" | "task_type";

export function Costs() {
	const [groupBy, setGroupBy] = useState<GroupBy>("day");
	const [days, setDays] = useState(30);
	const report = useQuery({
		queryKey: ["costs", groupBy, days],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/costs", {
					params: { query: { group_by: groupBy, days } },
				}),
			),
	});
	const rows = report.data?.rows ?? [];
	const notional = rows.some((r) => r.notional);
	return (
		<>
			<PageHeader title="Costs">
				<Tabs
					label="Group by"
					value={groupBy}
					onChange={setGroupBy}
					options={[
						{ value: "day", label: "Day" },
						{ value: "agent", label: "Agent" },
						{ value: "model", label: "Model" },
						{ value: "team", label: "Team" },
						{ value: "task_type", label: "Task type" },
					]}
				/>
				<label
					className="flex items-center gap-2 text-sm"
					htmlFor="cost-window"
				>
					<span className="text-muted">Window</span>
					<Select
						id="cost-window"
						value={days}
						onChange={(e) => setDays(Number(e.target.value))}
						className="w-28"
					>
						{[7, 30, 90, 365].map((d) => (
							<option key={d} value={d}>
								{d} days
							</option>
						))}
					</Select>
				</label>
			</PageHeader>
			<ErrorNote error={report.error} />
			{report.isPending ? (
				<Spinner />
			) : rows.length === 0 ? (
				<Empty>No model usage in this window.</Empty>
			) : (
				<div className="space-y-4">
					<div className="grid gap-3 md:grid-cols-3">
						<Stat
							icon={CircleDollarSign}
							label={notional ? "Total (incl. notional)" : "Total"}
							value={formatUsd(report.data?.total_usd ?? 0)}
						/>
						<Stat
							icon={PlayCircle}
							label="Runs"
							value={rows.reduce((a, r) => a + r.runs, 0)}
						/>
						<Stat
							icon={Wrench}
							label="Tool calls"
							value={report.data?.tool_calls ?? 0}
						/>
					</div>
					{notional && (
						<p className="px-1 text-xs text-muted">
							Includes notional prices for self-hosted models.
						</p>
					)}
					<Card title={`Cost by ${groupBy.replace("_", " ")}`}>
						<div
							className="h-80"
							role="img"
							aria-label={`Bar chart of cost by ${groupBy}; the table below has the same data`}
						>
							<ResponsiveContainer width="100%" height="100%">
								<BarChart data={rows}>
									<defs>
										<linearGradient id="cost-bar" x1="0" y1="0" x2="0" y2="1">
											<stop
												offset="0"
												stopColor="var(--accent)"
												stopOpacity={1}
											/>
											<stop
												offset="1"
												stopColor="var(--accent)"
												stopOpacity={0.55}
											/>
										</linearGradient>
									</defs>
									<CartesianGrid vertical={false} stroke="var(--border)" />
									<XAxis
										dataKey="key"
										stroke="var(--muted)"
										fontSize={12}
										tickLine={false}
										axisLine={false}
									/>
									<YAxis
										stroke="var(--muted)"
										fontSize={12}
										tickLine={false}
										axisLine={false}
										width={64}
										tickFormatter={(v: number) => formatUsd(v)}
									/>
									<Tooltip
										formatter={(v) => formatUsd(Number(v))}
										cursor={{ fill: "var(--fill)" }}
										contentStyle={{
											background: "var(--surface)",
											border: "none",
											borderRadius: 12,
											boxShadow: "var(--shadow-pop)",
											color: "var(--fg)",
										}}
									/>
									<Bar
										dataKey="cost_usd"
										name="Cost"
										fill="url(#cost-bar)"
										radius={[6, 6, 0, 0]}
										maxBarSize={48}
									/>
								</BarChart>
							</ResponsiveContainer>
						</div>
					</Card>
					<Card title="Data">
						<table className="w-full text-sm">
							<caption className="sr-only">Costs</caption>
							<thead className="text-left text-muted">
								<tr>
									<th scope="col" className="py-1 font-medium">
										{groupBy.replace("_", " ")}
									</th>
									<th scope="col" className="py-1 text-right font-medium">
										Runs
									</th>
									<th scope="col" className="py-1 text-right font-medium">
										Calls
									</th>
									<th scope="col" className="py-1 text-right font-medium">
										Tokens
									</th>
									<th scope="col" className="py-1 text-right font-medium">
										Cost
									</th>
								</tr>
							</thead>
							<tbody>
								{rows.map((r) => (
									<tr key={r.key} className="border-t border-border">
										<th scope="row" className="py-1 text-left font-normal">
											{r.key}
										</th>
										<td className="py-1 text-right tabular-nums">{r.runs}</td>
										<td className="py-1 text-right tabular-nums">
											{r.llm_calls}
										</td>
										<td className="py-1 text-right tabular-nums">
											{formatTokens(r.input_tokens + r.output_tokens)}
										</td>
										<td className="py-1 text-right tabular-nums">
											{formatUsd(r.cost_usd)}
											{r.notional && (
												<span className="ml-1 text-xs text-muted">
													(notional)
												</span>
											)}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</Card>
				</div>
			)}
		</>
	);
}
