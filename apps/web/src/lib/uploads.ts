// Attachment uploads, shared by the Home ask box and the New task form (FR-02 limits).
import { useState } from "react";
import { api, type Schemas, unwrap } from "./api/client";

export const MAX_FILES = 5;
export const MAX_BYTES = 10 * 1024 * 1024;
export const EXTENSIONS = [".pdf", ".md", ".txt", ".csv", ".xlsx"];

export interface Upload {
	id: string;
	name: string;
	bytes: number;
	artifactId?: string;
	error?: string;
}

/** Why a file is refused before upload, or null when it is acceptable. */
export function rejectReason(name: string, bytes: number): string | null {
	const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
	if (!EXTENSIONS.includes(ext)) return "type not allowed";
	if (bytes > MAX_BYTES) return "over 10 MB";
	return null;
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

export function useUploads() {
	const [uploads, setUploads] = useState<Upload[]>([]);

	const add = async (files: FileList | File[]) => {
		let count = uploads.length;
		for (const file of Array.from(files)) {
			if (count + 1 > MAX_FILES) break;
			count += 1;
			const entry: Upload = {
				id: crypto.randomUUID(),
				name: file.name,
				bytes: file.size,
			};
			const reason = rejectReason(file.name, file.size);
			if (reason) entry.error = reason;
			setUploads((u) => [...u, entry]);
			if (entry.error) continue;
			try {
				const accepted = await uploadFile(file);
				setUploads((u) =>
					u.map((x) =>
						x.id === entry.id ? { ...x, artifactId: accepted.artifact_id } : x,
					),
				);
			} catch (e) {
				setUploads((u) =>
					u.map((x) =>
						x.id === entry.id
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

	const remove = (id: string) =>
		setUploads((u) => u.filter((x) => x.id !== id));
	const clear = () => setUploads([]);
	const artifactIds = uploads.flatMap((u) =>
		u.artifactId ? [u.artifactId] : [],
	);
	const busy = uploads.some((u) => !u.artifactId && !u.error);

	return { uploads, add, remove, clear, artifactIds, busy };
}
