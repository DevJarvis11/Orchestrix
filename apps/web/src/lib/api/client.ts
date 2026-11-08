// Generated, typed API client (SPEC 18.2): types come from `pnpm gen:api`, never by hand.
import createClient, { type Middleware } from "openapi-fetch";
import { getToken } from "../auth";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export const API_URL: string =
	import.meta.env.VITE_API_URL ?? "http://localhost:58100";

const auth: Middleware = {
	onRequest({ request }) {
		const token = getToken();
		if (token) request.headers.set("Authorization", `Bearer ${token}`);
		return request;
	},
};

export const api = createClient<paths>({ baseUrl: API_URL });
api.use(auth);

/** RFC 9457 problem details, as the API returns them. */
export interface Problem {
	title?: string;
	status?: number;
	detail?: string;
}

export class ApiError extends Error {
	readonly status: number;
	constructor(status: number, problem: Problem | undefined) {
		super(problem?.detail ?? problem?.title ?? `HTTP ${status}`);
		this.status = status;
	}
}

function isProblem(value: unknown): value is Problem {
	return typeof value === "object" && value !== null;
}

/** Unwrap an openapi-fetch result: data, or an ApiError carrying the problem detail. */
export function unwrap<T>(result: {
	data?: T;
	error?: unknown;
	response: Response;
}): T {
	if (result.error !== undefined || result.data === undefined) {
		throw new ApiError(
			result.response.status,
			isProblem(result.error) ? result.error : undefined,
		);
	}
	return result.data;
}

/** Authenticated fetch for the few endpoints openapi-fetch does not model well (redirects, CSV). */
export async function authedFetch(
	path: string,
	init: RequestInit = {},
): Promise<Response> {
	const headers = new Headers(init.headers);
	const token = getToken();
	if (token) headers.set("Authorization", `Bearer ${token}`);
	return fetch(`${API_URL}${path}`, { ...init, headers });
}
