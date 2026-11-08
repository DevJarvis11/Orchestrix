// New task (SPEC 18.1, FR-02): Zod schema mirrors the API limits; uploads go first.
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { FileUp, Trash2 } from "lucide-react";
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

const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;
const EXTENSIONS = [".pdf", ".md", ".txt", ".csv", ".xlsx"];
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

interface Upload {
	id: string;
	name: string;
	bytes: number;
	artifactId?: string;
	error?: string;
}

async function uploadFile(file: File): Promise<Schemas["UploadAccepted"]> {
	const form = new FormData();
	form.append("file", file);
	return unwrap(
		await api.POST("/v1/uploads", {
			// multipart: the generated body type is the form, sent as FormData unchanged
			body: form as unknown as Schemas["Body_upload_v1_uploads_post"],
			bodySerializer: (b) => b as unknown as FormData,
		}),
	);
}

export function NewTask() {
	const navigate = useNavigate();
	const [uploads, setUploads] = useState<Upload[]>([]);
	const [dragging, setDragging] = useState(false);
	const models = useQuery({
		queryKey: ["models"],
		queryFn: async () => unwrap(await api.GET("/v1/models")),
	});
	const form = useForm<FormIn, unknown, FormOut>({
		resolver: zodResolver(schema),
		defaultValues: {
			title: "",
			description: "",
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

	const addFiles = async (files: FileList | File[]) => {
		const list = Array.from(files);
		for (const file of list) {
			if (uploads.length + 1 > MAX_FILES) break;
			const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
			const entry: Upload = {
				id: crypto.randomUUID(),
				name: file.name,
				bytes: file.size,
			};
			if (!EXTENSIONS.includes(ext)) entry.error = "type not allowed";
			else if (file.size > MAX_BYTES) entry.error = "over 10 MB";
			setUploads((u) => [...u, entry]);
			if (entry.error) continue;
			try {
				const accepted = await uploadFile(file);
				setUploads((u) =>
					u.map((x) =>
						x === entry ? { ...x, artifactId: accepted.artifact_id } : x,
					),
				);
			} catch (e) {
				setUploads((u) =>
					u.map((x) =>
						x === entry
							? {
									...x,
									error: e instanceof Error ? e.message : "upload failed",
								}
							: x,
					),
				);
			}
		}
	};

	const submit = useMutation({
		mutationFn: async (v: FormOut) => {
			const overrides: Record<string, string> = {};
			for (const role of ROLES) if (v[role]) overrides[role] = v[role];
			const num = (x: number | "" | undefined, fallback: number) =>
				x === "" || x === undefined ? fallback : x;
			const body: Schemas["TaskCreate"] = {
				title: v.title,
				description: v.description,
				attachments: uploads.flatMap((u) =>
					u.artifactId ? [u.artifactId] : [],
				),
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

	const busyUploads = uploads.some((u) => !u.artifactId && !u.error);
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
									void addFiles(e.dataTransfer.files);
								}}
								className={`flex cursor-pointer flex-col items-center gap-1 rounded-md border-2 border-dashed p-6 text-sm text-muted ${dragging ? "border-accent bg-surface-2" : "border-border"}`}
							>
								<FileUp aria-hidden className="size-5" />
								Drop files or choose: pdf, md, txt, csv, xlsx · up to{" "}
								{MAX_FILES} · 10 MB each
								<input
									type="file"
									multiple
									accept={EXTENSIONS.join(",")}
									className="sr-only"
									onChange={(e) =>
										e.target.files && void addFiles(e.target.files)
									}
									disabled={uploads.length >= MAX_FILES}
								/>
							</label>
							{uploads.length > 0 && (
								<ul className="mt-2 space-y-1 text-sm">
									{uploads.map((u) => (
										<li
											key={u.id}
											className="flex items-center justify-between rounded border border-border px-2 py-1"
										>
											<span>
												{u.name}{" "}
												<span className="text-muted">
													({Math.ceil(u.bytes / 1024)} KB)
												</span>{" "}
												{u.error ? (
													<span className="text-danger">— {u.error}</span>
												) : u.artifactId ? (
													<span className="text-st-approved">— uploaded</span>
												) : (
													<span className="text-muted">— uploading…</span>
												)}
											</span>
											<Button
												variant="ghost"
												size="sm"
												aria-label={`Remove ${u.name}`}
												onClick={() =>
													setUploads((x) => x.filter((y) => y.id !== u.id))
												}
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
						Submit task
					</Button>
				</div>
			</form>
		</>
	);
}
