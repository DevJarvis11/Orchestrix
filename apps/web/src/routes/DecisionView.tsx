// Decision view (SPEC 18.1, 14.2; journeys 5.2, 5.3; FR-43, FR-44).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { MessageSquare, Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/Layout";
import { Markdown } from "../components/Markdown";
import { StatusBadge } from "../components/StatusBadge";
import {
	Button,
	Card,
	ErrorNote,
	Field,
	Input,
	JsonEditor,
	Kv,
	Select,
	Spinner,
	Textarea,
} from "../components/ui";
import { ApiError, api, type Schemas, unwrap } from "../lib/api/client";
import { keys } from "../lib/events";
import { SlaCountdown, useNow } from "./Inbox";

type Action = Schemas["DecisionBody"]["action"];
type Json = Record<string, unknown>;

interface JsonSchemaProp {
	type?: string | string[];
	items?: { type?: string };
	title?: string;
	description?: string;
	enum?: unknown[];
	anyOf?: JsonSchemaProp[];
}

function propType(p: JsonSchemaProp): string {
	if (typeof p.type === "string") return p.type;
	if (Array.isArray(p.type))
		return p.type.find((t) => t !== "null") ?? "string";
	const first = p.anyOf?.find((a) => a.type !== "null");
	return first ? propType(first) : "string";
}

/** A form generated from the tool's JSON input schema (SPEC 18.1); JSON editor for the rest. */
function SchemaForm({
	schema,
	value,
	onChange,
}: {
	schema: Json;
	value: Json;
	onChange: (v: Json) => void;
}) {
	const props = (schema.properties ?? {}) as Record<string, JsonSchemaProp>;
	return (
		<div className="space-y-3">
			{Object.entries(props).map(([name, prop]) => {
				const type = propType(prop);
				const current = value[name];
				const label = prop.title ?? name;
				if (type === "boolean") {
					return (
						<label key={name} className="flex items-center gap-2 text-sm">
							<input
								type="checkbox"
								className="size-4"
								checked={current === true}
								onChange={(e) =>
									onChange({ ...value, [name]: e.target.checked })
								}
							/>
							{label}
						</label>
					);
				}
				if (type === "array") {
					const list = Array.isArray(current) ? current.map(String) : [];
					return (
						<Field
							key={name}
							label={`${label} (one per line)`}
							hint={prop.description}
						>
							{(id) => (
								<Textarea
									id={id}
									rows={Math.max(3, list.length + 1)}
									value={list.join("\n")}
									onChange={(e) =>
										onChange({
											...value,
											[name]: e.target.value
												.split("\n")
												.map((s) => s.trim())
												.filter(Boolean),
										})
									}
								/>
							)}
						</Field>
					);
				}
				if (type === "number" || type === "integer") {
					return (
						<Field key={name} label={label} hint={prop.description}>
							{(id) => (
								<Input
									id={id}
									type="number"
									value={typeof current === "number" ? current : ""}
									onChange={(e) =>
										onChange({
											...value,
											[name]:
												e.target.value === ""
													? undefined
													: Number(e.target.value),
										})
									}
								/>
							)}
						</Field>
					);
				}
				if (type === "object") return null; // edited in the JSON view
				const text =
					typeof current === "string"
						? current
						: current == null
							? ""
							: JSON.stringify(current);
				return (
					<Field key={name} label={label} hint={prop.description}>
						{(id) =>
							text.length > 80 || name.includes("body") || name === "code" ? (
								<Textarea
									id={id}
									rows={8}
									value={text}
									onChange={(e) =>
										onChange({ ...value, [name]: e.target.value })
									}
								/>
							) : (
								<Input
									id={id}
									value={text}
									onChange={(e) =>
										onChange({ ...value, [name]: e.target.value })
									}
								/>
							)
						}
					</Field>
				);
			})}
		</div>
	);
}

function asJson(v: unknown): Json {
	return typeof v === "object" && v !== null && !Array.isArray(v)
		? (v as Json)
		: {};
}

export function DecisionView() {
	const { id } = useParams({ from: "/escalations/$id" });
	const qc = useQueryClient();
	const navigate = useNavigate();
	const now = useNow();
	const detail = useQuery({
		queryKey: [...keys.escalations, "detail", id],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/escalations/{escalation_id}", {
					params: { path: { escalation_id: id } },
				}),
			),
	});
	const payload = asJson(detail.data?.payload);
	const proposed = asJson(payload.proposed);
	const toolName = typeof proposed.tool === "string" ? proposed.tool : null;
	const plan = proposed.plan ? asJson(proposed.plan) : null;
	const budget = proposed.budget ? asJson(proposed.budget) : null;
	const draft = asJson(proposed.draft);
	const deliverable =
		typeof proposed.deliverable === "string" ? proposed.deliverable : null;
	const allowed = (
		Array.isArray(payload.allowed_actions) ? payload.allowed_actions : []
	) as Action[];
	const questions = (
		Array.isArray(payload.questions) ? payload.questions : []
	).map(String);

	const tool = useQuery({
		queryKey: ["tool", toolName],
		enabled: !!toolName,
		retry: false, // reviewers may get 403: fall back to the JSON editor
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/admin/tools/{name}", {
					params: { path: { name: toolName ?? "" } },
				}),
			),
	});

	const editable = useMemo<Json>(
		() => (toolName ? asJson(proposed.args) : (plan ?? budget ?? {})),
		[toolName, proposed.args, plan, budget],
	);
	const [edited, setEdited] = useState<Json>({});
	const [jsonText, setJsonText] = useState("{}");
	const [mode, setMode] = useState<"form" | "json">("form");
	const [reason, setReason] = useState("");
	const [onReject, setOnReject] = useState<"retry" | "skip" | "fail_task">(
		"retry",
	);
	const [output, setOutput] = useState("");
	const [answers, setAnswers] = useState<string[]>([]);
	const key = detail.data?.id;
	// Reset the editors only when a different escalation loads, not on every refetch.
	// biome-ignore lint/correctness/useExhaustiveDependencies: keyed on the escalation id
	useEffect(() => {
		if (!key) return;
		setEdited(editable);
		setJsonText(JSON.stringify(editable, null, 2));
		setOutput(
			deliverable ??
				(typeof draft.output === "string"
					? draft.output
					: plan
						? JSON.stringify(plan, null, 2)
						: ""),
		);
		setAnswers(questions.map(() => ""));
	}, [key]);

	const decide = useMutation({
		mutationFn: async (action: Action) => {
			let edits: Json | null = null;
			if (action === "approve_with_edits") {
				edits = mode === "json" ? (JSON.parse(jsonText) as Json) : edited;
			}
			const body: Schemas["DecisionBody"] = {
				action,
				edits,
				reason: action === "reject" ? reason : reason || null,
				on_reject: action === "reject" ? onReject : null,
				output: action === "take_over" ? output : null,
				answers: action === "answer" ? answers : null,
			};
			return unwrap(
				await api.POST("/v1/escalations/{escalation_id}/decision", {
					params: { path: { escalation_id: id } },
					body,
				}),
			);
		},
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: keys.escalations });
			void navigate({ to: "/escalations" });
		},
	});

	if (detail.isPending) return <Spinner />;
	if (detail.error) return <ErrorNote error={detail.error} />;
	const d = detail.data;
	const canDecide = d.can_decide && d.status === "open";
	const progress = (
		Array.isArray(payload.plan_progress) ? payload.plan_progress : []
	) as { key: string; specialist: string; status: string }[];
	const memories = (
		Array.isArray(payload.related_memories) ? payload.related_memories : []
	) as { id: string; text: string }[];
	const editsJsonValid = (() => {
		try {
			JSON.parse(jsonText);
			return true;
		} catch {
			return false;
		}
	})();

	const removeSubtask = (k: string) => {
		const subtasks = (
			Array.isArray(edited.subtasks) ? edited.subtasks : []
		) as Json[];
		const next = {
			...edited,
			subtasks: subtasks
				.filter((s) => s.key !== k)
				.map((s) => ({
					...s,
					depends_on: (Array.isArray(s.depends_on) ? s.depends_on : []).filter(
						(x) => x !== k,
					),
				})),
		};
		setEdited(next);
		setJsonText(JSON.stringify(next, null, 2));
	};

	return (
		<>
			<PageHeader title={String(payload.title ?? d.title)}>
				<StatusBadge status={d.status} />
				{d.status === "open" && <SlaCountdown due={d.due_at} now={now} />}
			</PageHeader>
			<div className="grid gap-4 xl:grid-cols-[1fr_26rem]">
				<div className="min-w-0 space-y-4">
					<Card title="Context">
						<Kv
							items={[
								[
									"Task",
									<Link
										key="t"
										to="/tasks/$taskId"
										params={{ taskId: d.task_id }}
										className="text-accent hover:underline"
									>
										{d.task_title}
									</Link>,
								],
								["Trigger", d.trigger.replace(/_/g, " ")],
								["Level", d.level.replace(/_/g, " ")],
								["Summary", String(payload.task_summary ?? "")],
							]}
						/>
						{progress.length > 0 && (
							<ul
								className="mt-3 flex flex-wrap gap-2"
								aria-label="Plan progress"
							>
								{progress.map((p) => (
									<li
										key={p.key}
										className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs"
									>
										{p.key} · {p.specialist} <StatusBadge status={p.status} />
									</li>
								))}
							</ul>
						)}
					</Card>
					<Card title="Agent's reasoning">
						<p className="whitespace-pre-wrap text-sm">
							{String(payload.reasoning ?? "—")}
						</p>
					</Card>
					<Card
						title="Proposed"
						actions={
							(toolName || plan || budget) && (
								<div className="flex gap-1">
									<Button
										size="sm"
										variant={mode === "form" ? "primary" : "ghost"}
										onClick={() => {
											setMode("form");
											try {
												setEdited(JSON.parse(jsonText) as Json);
											} catch {
												/* keep the form */
											}
										}}
									>
										Form
									</Button>
									<Button
										size="sm"
										variant={mode === "json" ? "primary" : "ghost"}
										onClick={() => {
											setMode("json");
											setJsonText(JSON.stringify(edited, null, 2));
										}}
									>
										JSON
									</Button>
								</div>
							)
						}
					>
						{toolName && (
							<p className="mb-3 text-sm">
								Tool <code className="font-mono">{toolName}</code>
								{Array.isArray(proposed.reasons) &&
									proposed.reasons.length > 0 && (
										<span className="text-muted">
											{" "}
											— {proposed.reasons.join("; ")}
										</span>
									)}
							</p>
						)}
						{mode === "json" || (toolName && !tool.data) ? (
							<>
								{toolName && tool.error instanceof ApiError && (
									<p className="mb-2 text-xs text-muted">
										The tool schema is not visible to your role; editing the
										arguments as JSON.
									</p>
								)}
								<JsonEditor
									label="Edits (sent with Approve with edits)"
									value={jsonText}
									onChange={(t) => {
										setJsonText(t);
										setMode("json");
									}}
								/>
							</>
						) : toolName && tool.data ? (
							<SchemaForm
								schema={asJson(tool.data.input_schema)}
								value={edited}
								onChange={(v) => {
									setEdited(v);
									setJsonText(JSON.stringify(v, null, 2));
								}}
							/>
						) : plan ? (
							<div className="space-y-2">
								<p className="text-sm">{String(edited.goal ?? "")}</p>
								<ul className="space-y-1.5">
									{(
										(Array.isArray(edited.subtasks)
											? edited.subtasks
											: []) as Json[]
									).map((s) => (
										<li
											key={String(s.key)}
											className="flex items-start justify-between gap-2 rounded border border-border p-2 text-sm"
										>
											<span>
												<strong>{String(s.key)}</strong> ·{" "}
												{String(s.specialist)} — {String(s.description)}
												{Array.isArray(s.depends_on) &&
													s.depends_on.length > 0 && (
														<span className="text-muted">
															{" "}
															(after {s.depends_on.join(", ")})
														</span>
													)}
											</span>
											{canDecide && (
												<Button
													size="sm"
													variant="ghost"
													onClick={() => removeSubtask(String(s.key))}
													aria-label={`Remove subtask ${String(s.key)}`}
												>
													Remove
												</Button>
											)}
										</li>
									))}
								</ul>
								{Array.isArray(proposed.errors) &&
									proposed.errors.length > 0 && (
										<ul className="list-disc pl-5 text-sm text-danger">
											{proposed.errors.map((e) => (
												<li key={String(e)}>{String(e)}</li>
											))}
										</ul>
									)}
							</div>
						) : budget ? (
							<SchemaForm
								schema={{
									properties: Object.fromEntries(
										Object.keys(budget).map((k) => [
											k,
											{ type: "number", title: k },
										]),
									),
								}}
								value={edited}
								onChange={(v) => {
									setEdited(v);
									setJsonText(JSON.stringify(v, null, 2));
								}}
							/>
						) : deliverable ? (
							<Markdown>{deliverable}</Markdown>
						) : draft.output ? (
							<Markdown>{String(draft.output)}</Markdown>
						) : (
							<pre className="overflow-x-auto text-xs">
								{JSON.stringify(proposed, null, 2)}
							</pre>
						)}
					</Card>
					{memories.length > 0 && (
						<Card title="Related memories">
							<ul className="space-y-1 text-sm">
								{memories.map((m) => (
									<li key={m.id}>{m.text}</li>
								))}
							</ul>
						</Card>
					)}
				</div>
				<div className="space-y-4">
					<Card title="Decision">
						{!canDecide ? (
							<p className="text-sm text-muted">
								{d.status !== "open"
									? `This escalation is ${d.status}.`
									: "You cannot decide this escalation (your role, your team, or it is your own task)."}
							</p>
						) : (
							<form className="space-y-3" onSubmit={(e) => e.preventDefault()}>
								{allowed.includes("answer") &&
									questions.map((q, i) => (
										<Field key={q} label={q}>
											{(fid) => (
												<Textarea
													id={fid}
													rows={2}
													value={answers[i] ?? ""}
													onChange={(e) =>
														setAnswers((a) =>
															a.map((x, j) => (j === i ? e.target.value : x)),
														)
													}
												/>
											)}
										</Field>
									))}
								{allowed.includes("take_over") && (
									<Field
										label={
											plan
												? "Take over: your plan (JSON)"
												: "Take over: your output"
										}
									>
										{(fid) => (
											<Textarea
												id={fid}
												rows={8}
												value={output}
												onChange={(e) => setOutput(e.target.value)}
												className={plan ? "font-mono text-xs" : ""}
											/>
										)}
									</Field>
								)}
								{allowed.includes("reject") && (
									<>
										<Field label="Reason (required to reject)">
											{(fid) => (
												<Textarea
													id={fid}
													rows={2}
													value={reason}
													onChange={(e) => setReason(e.target.value)}
												/>
											)}
										</Field>
										{(d.subtask || d.level === "take_over") && (
											<Field label="After rejecting">
												{(fid) => (
													<Select
														id={fid}
														value={onReject}
														onChange={(e) =>
															setOnReject(e.target.value as typeof onReject)
														}
													>
														<option value="retry">Retry with my note</option>
														<option value="skip">Skip this subtask</option>
														<option value="fail_task">Fail the task</option>
													</Select>
												)}
											</Field>
										)}
									</>
								)}
								<div className="flex flex-wrap gap-2">
									{allowed.includes("approve") && (
										<Button
											variant="primary"
											busy={decide.isPending}
											onClick={() => decide.mutate("approve")}
										>
											Approve
										</Button>
									)}
									{allowed.includes("approve_with_edits") && (
										<Button
											variant="primary"
											busy={decide.isPending}
											disabled={mode === "json" && !editsJsonValid}
											onClick={() => decide.mutate("approve_with_edits")}
										>
											Approve with edits
										</Button>
									)}
									{allowed.includes("answer") && (
										<Button
											variant="primary"
											busy={decide.isPending}
											disabled={answers.some((a) => !a.trim())}
											onClick={() => decide.mutate("answer")}
										>
											Send answers
										</Button>
									)}
									{allowed.includes("take_over") && (
										<Button
											busy={decide.isPending}
											disabled={!output.trim()}
											onClick={() => decide.mutate("take_over")}
										>
											Take over
										</Button>
									)}
									{allowed.includes("reject") && (
										<Button
											variant="danger"
											busy={decide.isPending}
											disabled={!reason.trim()}
											onClick={() => decide.mutate("reject")}
										>
											Reject
										</Button>
									)}
								</div>
								<ErrorNote error={decide.error} />
							</form>
						)}
						{d.actions.length > 0 && (
							<ul className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-muted">
								{d.actions.map((a) => (
									<li key={a.created_at}>
										{a.action} by {a.actor_id.slice(0, 8)} ·{" "}
										{new Date(a.created_at).toLocaleString()}
									</li>
								))}
							</ul>
						)}
					</Card>
					<Chat id={id} messages={d.messages} enabled={d.can_decide} />
				</div>
			</div>
		</>
	);
}

function Chat({
	id,
	messages,
	enabled,
}: {
	id: string;
	messages: Schemas["MessageView"][];
	enabled: boolean;
}) {
	const qc = useQueryClient();
	const [text, setText] = useState("");
	const send = useMutation({
		mutationFn: async (message: string) =>
			unwrap(
				await api.POST("/v1/escalations/{escalation_id}/chat", {
					params: { path: { escalation_id: id } },
					body: { message },
				}),
			),
		onSuccess: () => {
			setText("");
			void qc.invalidateQueries({
				queryKey: [...keys.escalations, "detail", id],
			});
		},
	});
	return (
		<Card
			title={
				<span className="inline-flex items-center gap-1.5">
					<MessageSquare aria-hidden className="size-4" /> Ask the agent
				</span>
			}
		>
			<ol
				aria-live="polite"
				className="mb-3 max-h-80 space-y-2 overflow-y-auto text-sm"
			>
				{messages.length === 0 && (
					<li className="text-muted">
						Ask why the agent proposed this. Answers come from the run's state;
						the chat cannot change the run.
					</li>
				)}
				{messages.map((m) => (
					<li
						key={m.created_at + m.role}
						className={
							m.role === "agent"
								? "rounded-md bg-surface-2 p-2"
								: "p-2 text-right"
						}
					>
						<span className="block text-xs text-muted">
							{m.role === "agent" ? "Agent" : "You"}
						</span>
						{m.role === "agent" ? <Markdown>{m.body}</Markdown> : m.body}
					</li>
				))}
			</ol>
			{enabled && (
				<form
					className="flex gap-2"
					onSubmit={(e) => {
						e.preventDefault();
						if (text.trim()) send.mutate(text.trim());
					}}
				>
					<label className="sr-only" htmlFor="chat-input">
						Question
					</label>
					<Input
						id="chat-input"
						value={text}
						onChange={(e) => setText(e.target.value)}
						placeholder="Why these recipients?"
						maxLength={2000}
					/>
					<Button
						type="submit"
						variant="primary"
						busy={send.isPending}
						aria-label="Send question"
					>
						<Send aria-hidden className="size-4" />
					</Button>
				</form>
			)}
			<ErrorNote error={send.error} />
		</Card>
	);
}
