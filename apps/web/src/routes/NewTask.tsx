// New task (SPEC 18.1, FR-02): Zod schema mirrors the API limits; uploads go first.
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { CheckCircle2, FileText, FileUp, Loader2, Trash2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { PageHeader } from "../components/Layout";
import {
	Button,
	Card,
	ErrorNote,
	Field,
	Input,
	Select,
	Textarea,
} from "../components/ui";
import { api, type Schemas, unwrap } from "../lib/api/client";
import { EXTENSIONS, MAX_FILES, useUploads } from "../lib/uploads";
import { cn, titleFromPrompt } from "../lib/utils";

const ROLES = ["planner", "specialist", "reviewer", "fast"] as const;

const optionalNumber = (min: number) =>
	z.union([z.literal(""), z.coerce.number().min(min)]).optional();

const schema = z.object({
	title: z.string().trim().min(1, "Required").max(200),
	description: z
		.string()
		.trim()
		.min(1, "Required")
		.max(8000, "At most 8,000 characters"),
	require_plan_approval: z.boolean(),
	require_final_approval: z.boolean(),
	override_budget: z.boolean(),
	max_tokens: optionalNumber(1),
	max_usd: optionalNumber(0),
	max_seconds: optionalNumber(1),
	max_tool_calls: optionalNumber(1),
	max_steps: optionalNumber(1),
	planner: z.string(),
	specialist: z.string(),
	reviewer: z.string(),
	fast: z.string(),
});
type FormIn = z.input<typeof schema>;
type FormOut = z.output<typeof schema>;

export function NewTask() {
	const navigate = useNavigate();
	const { q } = useSearch({ from: "/tasks/new" });
	const files = useUploads();
	const uploads = files.uploads;
	const [dragging, setDragging] = useState(false);
	const models = useQuery({
		queryKey: ["models"],
		queryFn: async () => unwrap(await api.GET("/v1/models")),
	});
	const form = useForm<FormIn, unknown, FormOut>({
		resolver: zodResolver(schema),
		defaultValues: {
			title: q ? titleFromPrompt(q) : "",
			description: q ?? "",
			require_plan_approval: false,
			require_final_approval: false,
			override_budget: false,
			planner: "",
			specialist: "",
			reviewer: "",
			fast: "",
		},
	});
	const { register, handleSubmit, watch, formState } = form;
	const description = watch("description") ?? "";
	const overrideBudget = watch("override_budget");

	const submit = useMutation({
		mutationFn: async (v: FormOut) => {
			const overrides: Record<string, string> = {};
			for (const role of ROLES) if (v[role]) overrides[role] = v[role];
			const num = (x: number | "" | undefined, fallback: number) =>
				x === "" || x === undefined ? fallback : x;
			const body: Schemas["TaskCreate"] = {
				title: v.title,
				description: v.description,
				attachments: files.artifactIds,
				require_plan_approval: v.require_plan_approval,
				require_final_approval: v.require_final_approval,
				model_overrides: overrides,
				budget: v.override_budget
					? {
							max_tokens: num(v.max_tokens, 400_000),
							max_usd: num(v.max_usd, 2),
							max_seconds: num(v.max_seconds, 1800),
							max_tool_calls: num(v.max_tool_calls, 60),
							max_steps: num(v.max_steps, 150),
						}
					: null,
			};
			return unwrap(
				await api.POST("/v1/tasks", {
					body,
					params: { header: { "Idempotency-Key": crypto.randomUUID() } },
				}),
			);
		},
		onSuccess: (created) =>
			void navigate({
				to: "/tasks/$taskId",
				params: { taskId: created.task_id },
			}),
	});

	const busyUploads = files.busy;
	const chatModels = models.data?.models ?? [];

	return (
		<>
			<PageHeader title="New task" />
			<form
				onSubmit={handleSubmit((v) => submit.mutate(v))}
				className="grid gap-4 lg:grid-cols-[2fr_1fr]"
				noValidate
			>
				<Card title="Request">
					<div className="space-y-4">
						<Field label="Title" error={formState.errors.title?.message}>
							{(id) => (
								<Input
									id={id}
									{...register("title")}
									aria-invalid={!!formState.errors.title}
								/>
							)}
						</Field>
						<Field
							label="Description"
							error={formState.errors.description?.message}
							hint={`${description.length.toLocaleString()} / 8,000 characters`}
						>
							{(id) => (
								<Textarea
									id={id}
									rows={10}
									{...register("description")}
									aria-invalid={!!formState.errors.description}
								/>
							)}
						</Field>
						<div>
							<span className="mb-1 block text-sm font-medium">
								Attachments
							</span>
							<label
								onDragOver={(e) => {
									e.preventDefault();
									setDragging(true);
								}}
								onDragLeave={() => setDragging(false)}
								onDrop={(e) => {
									e.preventDefault();
									setDragging(false);
									void files.add(e.dataTransfer.files);
								}}
								className={cn(
									"flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-8 text-center text-sm text-muted transition-colors",
									dragging
										? "border-accent bg-accent/5 text-accent"
										: "border-border hover:border-faint hover:bg-fill/40",
								)}
							>
								<span className="grid size-11 place-items-center rounded-full bg-fill">
									<FileUp aria-hidden className="size-5" />
								</span>
								<span className="font-medium text-fg">
									Drop files here or click to choose
								</span>
								<span className="text-xs">
									pdf, md, txt, csv, xlsx · up to {MAX_FILES} · 10 MB each
								</span>
								<input
									type="file"
									multiple
									accept={EXTENSIONS.join(",")}
									className="sr-only"
									onChange={(e) =>
										e.target.files && void files.add(e.target.files)
									}
									disabled={uploads.length >= MAX_FILES}
								/>
							</label>
							{uploads.length > 0 && (
								<ul className="mt-3 space-y-1.5 text-sm">
									{uploads.map((u) => (
										<li
											key={u.id}
											className="flex items-center justify-between rounded-xl bg-fill px-3 py-1.5"
										>
											<span className="flex min-w-0 items-center gap-2">
												{u.error ? (
													<FileText
														aria-hidden
														className="size-4 shrink-0 text-danger"
													/>
												) : u.artifactId ? (
													<CheckCircle2
														aria-hidden
														className="size-4 shrink-0 text-st-approved"
													/>
												) : (
													<Loader2
														aria-hidden
														className="size-4 shrink-0 animate-spin text-muted"
													/>
												)}
												<span className="truncate">{u.name}</span>
												<span className="shrink-0 text-xs text-muted">
													{Math.ceil(u.bytes / 1024)} KB
													{u.error ? (
														<span className="text-danger"> · {u.error}</span>
													) : u.artifactId ? (
														" · uploaded"
													) : (
														" · uploading…"
													)}
												</span>
											</span>
											<Button
												variant="ghost"
												size="sm"
												aria-label={`Remove ${u.name}`}
												onClick={() => files.remove(u.id)}
											>
												<Trash2 aria-hidden className="size-4" />
											</Button>
										</li>
									))}
								</ul>
							)}
						</div>
					</div>
				</Card>
				<div className="space-y-4">
					<Card title="Approvals">
						<div className="space-y-2 text-sm">
							<label className="flex items-center gap-2">
								<input
									type="checkbox"
									className="size-4"
									{...register("require_plan_approval")}
								/>{" "}
								Approve the plan before it runs
							</label>
							<label className="flex items-center gap-2">
								<input
									type="checkbox"
									className="size-4"
									{...register("require_final_approval")}
								/>{" "}
								Approve the deliverable before delivery
							</label>
						</div>
					</Card>
					<Card title="Models (optional)">
						<div className="space-y-3">
							{ROLES.map((role) => (
								<Field key={role} label={role}>
									{(id) => (
										<Select id={id} {...register(role)}>
											<option value="">
												Default ({models.data?.defaults[role] ?? "…"})
											</option>
											{chatModels.map((m) => (
												<option key={m.id} value={m.id}>
													{m.id}
												</option>
											))}
										</Select>
									)}
								</Field>
							))}
							<ErrorNote error={models.error} />
						</div>
					</Card>
					<Card title="Budget">
						<label className="mb-3 flex items-center gap-2 text-sm">
							<input
								type="checkbox"
								className="size-4"
								{...register("override_budget")}
							/>{" "}
							Override the default budget
						</label>
						{overrideBudget && (
							<div className="grid grid-cols-2 gap-3">
								{(
									[
										["max_tokens", "Tokens"],
										["max_usd", "USD"],
										["max_seconds", "Active seconds"],
										["max_tool_calls", "Tool calls"],
										["max_steps", "Graph steps"],
									] as const
								).map(([name, label]) => (
									<Field
										key={name}
										label={label}
										error={formState.errors[name]?.message}
									>
										{(id) => (
											<Input
												id={id}
												type="number"
												min={0}
												step="any"
												{...register(name)}
											/>
										)}
									</Field>
								))}
							</div>
						)}
					</Card>
					<ErrorNote error={submit.error} />
					<Button
						type="submit"
						variant="primary"
						className="w-full"
						busy={submit.isPending}
						disabled={busyUploads}
					>
						Start task
					</Button>
				</div>
			</form>
		</>
	);
}
