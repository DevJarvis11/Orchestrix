// Memory (SPEC 18.1, FR-55; journey 5.5): search, filter by kind, edit, delete with confirm.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Pencil, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "../components/Layout";
import {
	Button,
	Empty,
	ErrorNote,
	Field,
	Input,
	Modal,
	Select,
	Spinner,
	Textarea,
} from "../components/ui";
import { api, type Schemas, unwrap } from "../lib/api/client";

const KINDS = ["episode", "lesson", "preference", "fact"] as const;
type Kind = (typeof KINDS)[number];
type MemoryView = Schemas["MemoryView"];

export function MemoryPage() {
	const qc = useQueryClient();
	const [kind, setKind] = useState<Kind | "">("");
	const [q, setQ] = useState("");
	const [search, setSearch] = useState("");
	const [editing, setEditing] = useState<MemoryView | null>(null);
	const [deleting, setDeleting] = useState<MemoryView | null>(null);
	const list = useQuery({
		queryKey: ["memories", kind, search],
		queryFn: async () =>
			unwrap(
				await api.GET("/v1/memories", {
					params: { query: { kind: kind || null, q: search || null } },
				}),
			),
	});
	const refresh = () => void qc.invalidateQueries({ queryKey: ["memories"] });
	const remove = useMutation({
		mutationFn: async (id: string) => {
			const r = await api.DELETE("/v1/memories/{memory_id}", {
				params: { path: { memory_id: id } },
			});
			if (!r.response.ok)
				throw new Error(`delete failed: HTTP ${r.response.status}`);
		},
		onSuccess: () => {
			setDeleting(null);
			refresh();
		},
	});
	const grouped = KINDS.map(
		(k) => [k, (list.data ?? []).filter((m) => m.kind === k)] as const,
	).filter(([, items]) => items.length > 0);
	return (
		<>
			<PageHeader title="Memory" />
			<form
				className="mb-4 flex flex-wrap items-end gap-3"
				onSubmit={(e) => {
					e.preventDefault();
					setSearch(q.trim());
				}}
			>
				<Field label="Search">
					{(id) => (
						<Input
							id={id}
							value={q}
							onChange={(e) => setQ(e.target.value)}
							className="w-80"
							maxLength={200}
						/>
					)}
				</Field>
				<Field label="Kind">
					{(id) => (
						<Select
							id={id}
							value={kind}
							onChange={(e) => setKind(e.target.value as Kind | "")}
							className="w-44"
						>
							<option value="">All kinds</option>
							{KINDS.map((k) => (
								<option key={k} value={k}>
									{k}
								</option>
							))}
						</Select>
					)}
				</Field>
				<Button type="submit">
					<Search aria-hidden className="size-4" /> Search
				</Button>
			</form>
			<ErrorNote error={list.error} />
			{list.isPending ? (
				<Spinner />
			) : grouped.length === 0 ? (
				<Empty>No memories yet. They are written after tasks complete.</Empty>
			) : (
				<div className="space-y-5">
					{grouped.map(([k, items]) => (
						<section key={k} aria-labelledby={`kind-${k}`}>
							<h2
								id={`kind-${k}`}
								className="mb-2 px-1 text-xs font-semibold tracking-wide text-muted uppercase"
							>
								{k}s ({items.length})
							</h2>
							<ul className="panel divide-y divide-border overflow-hidden">
								{items.map((m) => (
									<li key={m.id} className="flex items-start gap-3 px-4 py-3">
										<div className="min-w-0 flex-1">
											<p className="text-sm">{m.text}</p>
											<p className="mt-1 text-xs text-muted">
												{m.scope} · importance {m.importance.toFixed(2)} · used{" "}
												{m.access_count}×
												{m.derived_from.length > 0 &&
													` · merged from ${m.derived_from.length}`}
												{m.source_task_id && (
													<>
														{" · "}
														<Link
															to="/tasks/$taskId"
															params={{ taskId: m.source_task_id }}
															className="text-accent hover:underline"
														>
															source task
														</Link>
													</>
												)}
												{m.source_ref && ` · ${m.source_ref}`}
											</p>
										</div>
										{m.can_edit && (
											<div className="flex gap-1">
												<Button
													size="sm"
													variant="ghost"
													aria-label="Edit memory"
													onClick={() => setEditing(m)}
												>
													<Pencil aria-hidden className="size-4" />
												</Button>
												<Button
													size="sm"
													variant="ghost"
													aria-label="Delete memory"
													onClick={() => setDeleting(m)}
												>
													<Trash2 aria-hidden className="size-4" />
												</Button>
											</div>
										)}
									</li>
								))}
							</ul>
						</section>
					))}
				</div>
			)}
			<EditMemory
				memory={editing}
				onClose={() => setEditing(null)}
				onSaved={refresh}
			/>
			<Modal
				open={!!deleting}
				onClose={() => setDeleting(null)}
				title="Delete memory?"
			>
				<p className="text-sm">
					This permanently deletes the memory and any summary merged from it.
					The audit log records the deletion (without the text).
				</p>
				<blockquote className="my-3 rounded-xl bg-surface-2 px-4 py-3 text-sm text-muted">
					{deleting?.text}
				</blockquote>
				<ErrorNote error={remove.error} />
				<div className="flex justify-end gap-2">
					<Button onClick={() => setDeleting(null)}>Keep it</Button>
					<Button
						variant="danger"
						busy={remove.isPending}
						onClick={() => deleting && remove.mutate(deleting.id)}
					>
						Delete
					</Button>
				</div>
			</Modal>
		</>
	);
}

function EditMemory({
	memory,
	onClose,
	onSaved,
}: {
	memory: MemoryView | null;
	onClose: () => void;
	onSaved: () => void;
}) {
	const [text, setText] = useState("");
	const [importance, setImportance] = useState("0.5");
	const [loadedFor, setLoadedFor] = useState<string | null>(null);
	if (memory && loadedFor !== memory.id) {
		setLoadedFor(memory.id);
		setText(memory.text);
		setImportance(String(memory.importance));
	}
	const save = useMutation({
		mutationFn: async () => {
			if (!memory) return;
			unwrap(
				await api.PATCH("/v1/memories/{memory_id}", {
					params: { path: { memory_id: memory.id } },
					body: {
						text: text !== memory.text ? text : null,
						importance: Number(importance),
					},
				}),
			);
		},
		onSuccess: () => {
			onSaved();
			onClose();
		},
	});
	return (
		<Modal open={!!memory} onClose={onClose} title="Edit memory">
			<div className="space-y-3">
				<Field label="Text" hint={`${text.length}/500`}>
					{(id) => (
						<Textarea
							id={id}
							rows={5}
							maxLength={500}
							value={text}
							onChange={(e) => setText(e.target.value)}
						/>
					)}
				</Field>
				<Field label="Importance (0–1)">
					{(id) => (
						<Input
							id={id}
							type="number"
							min={0}
							max={1}
							step={0.05}
							value={importance}
							onChange={(e) => setImportance(e.target.value)}
						/>
					)}
				</Field>
				<ErrorNote error={save.error} />
				<div className="flex justify-end gap-2">
					<Button onClick={onClose}>Cancel</Button>
					<Button
						variant="primary"
						busy={save.isPending}
						onClick={() => save.mutate()}
						disabled={!text.trim()}
					>
						Save
					</Button>
				</div>
			</div>
		</Modal>
	);
}
