// Authentication (SPEC 15.4, 18.2): OIDC Authorization Code + PKCE against Keycloak with tokens
// held in memory only (never localStorage) and silent renew; or, in dev mode, a username/password
// exchanged at POST /v1/auth/login (a pasted `make dev-token` token still works via login()). Roles come from the token's realm_access.roles claim.
import {
	InMemoryWebStorage,
	type User,
	UserManager,
	WebStorageStateStore,
} from "oidc-client-ts";
import {
	createContext,
	type ReactNode,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useState,
} from "react";

export type Role = "requester" | "reviewer" | "operator" | "admin";
export const AUTH_MODE: "dev" | "oidc" =
	import.meta.env.VITE_AUTH_MODE === "oidc" ? "oidc" : "dev";
const ISSUER =
	import.meta.env.VITE_OIDC_ISSUER ?? "http://localhost:8180/realms/orchestrix";
const CLIENT_ID = import.meta.env.VITE_OIDC_CLIENT_ID ?? "orchestrix-web";

export interface Identity {
	sub: string;
	email: string;
	name: string;
	roles: Role[];
}

interface TokenClaims {
	sub?: string;
	email?: string;
	name?: string;
	preferred_username?: string;
	exp?: number;
	realm_access?: { roles?: string[] };
}

export function decodeClaims(token: string): TokenClaims {
	const part = token.split(".")[1];
	if (!part) throw new Error("not a JWT");
	const json = atob(
		part
			.replace(/-/g, "+")
			.replace(/_/g, "/")
			.padEnd(Math.ceil(part.length / 4) * 4, "="),
	);
	return JSON.parse(json) as TokenClaims;
}

const KNOWN_ROLES: readonly Role[] = [
	"requester",
	"reviewer",
	"operator",
	"admin",
];

export function identityOf(token: string): Identity {
	const c = decodeClaims(token);
	const roles = (c.realm_access?.roles ?? []).filter((r): r is Role =>
		(KNOWN_ROLES as readonly string[]).includes(r),
	);
	return {
		sub: c.sub ?? "",
		email: c.email ?? "",
		name: c.name ?? c.preferred_username ?? c.email ?? "user",
		roles,
	};
}

export function hasRole(identity: Identity | null, ...roles: Role[]): boolean {
	if (!identity) return false;
	return (
		identity.roles.includes("admin") ||
		roles.some((r) => identity.roles.includes(r))
	);
}

// The single token source for the API client and the SSE hook (module scope, in memory).
let currentToken: string | null = null;
export function getToken(): string | null {
	return currentToken;
}

export const userManager =
	AUTH_MODE === "oidc"
		? new UserManager({
				authority: ISSUER,
				client_id: CLIENT_ID,
				redirect_uri: `${window.location.origin}/auth/callback`,
				silent_redirect_uri: `${window.location.origin}/auth/silent`, // hidden-iframe renew
				post_logout_redirect_uri: window.location.origin,
				response_type: "code",
				scope: "openid profile email",
				automaticSilentRenew: true,
				userStore: new WebStorageStateStore({
					store: new InMemoryWebStorage(),
				}),
				stateStore: new WebStorageStateStore({ store: window.sessionStorage }), // PKCE verifier survives the redirect
			})
		: null;

interface AuthState {
	identity: Identity | null;
	token: string | null;
	ready: boolean;
	login: (devToken?: string) => Promise<void>;
	loginWithPassword: (username: string, password: string) => Promise<void>;
	logout: () => Promise<void>;
	error: string | null;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
	const [token, setToken] = useState<string | null>(null);
	const [ready, setReady] = useState(AUTH_MODE === "dev");
	const [error, setError] = useState<string | null>(null);

	const accept = useCallback((value: string | null) => {
		currentToken = value;
		setToken(value);
	}, []);

	useEffect(() => {
		if (!userManager) return;
		const onLoaded = (user: User) => accept(user.access_token);
		const onUnloaded = () => accept(null);
		userManager.events.addUserLoaded(onLoaded);
		userManager.events.addUserUnloaded(onUnloaded);
		const finish = async () => {
			try {
				if (window.location.pathname === "/auth/silent") {
					// Inside the hidden renew iframe: hand the result to the parent window and stop.
					await userManager.signinSilentCallback();
					return;
				}
				if (window.location.pathname === "/auth/callback") {
					const user = await userManager.signinRedirectCallback();
					accept(user.access_token);
					const target = typeof user.state === "string" ? user.state : "/";
					window.history.replaceState(null, "", target);
				} else {
					// Tokens live in memory, so a reload asks Keycloak again (its session cookie
					// makes this silent when the user is still signed in).
					const user = await userManager.signinSilent().catch(() => null);
					if (user) accept(user.access_token);
				}
			} catch (e) {
				setError(e instanceof Error ? e.message : String(e));
			} finally {
				setReady(true);
			}
		};
		void finish();
		return () => {
			userManager.events.removeUserLoaded(onLoaded);
			userManager.events.removeUserUnloaded(onUnloaded);
		};
	}, [accept]);

	const login = useCallback(
		async (devToken?: string) => {
			setError(null);
			if (AUTH_MODE === "dev") {
				const value = (devToken ?? "").trim();
				try {
					const claims = decodeClaims(value);
					if (claims.exp && claims.exp * 1000 < Date.now())
						throw new Error("token expired");
				} catch (e) {
					setError(
						e instanceof Error
							? `Invalid token: ${e.message}`
							: "Invalid token",
					);
					return;
				}
				accept(value);
				return;
			}
			await userManager?.signinRedirect({
				state: window.location.pathname + window.location.search,
			});
		},
		[accept],
	);

	const loginWithPassword = useCallback(
		async (username: string, password: string) => {
			setError(null);
			try {
				const base = import.meta.env.VITE_API_URL ?? "http://localhost:58100";
				const res = await fetch(`${base}/v1/auth/login`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ username, password }),
				});
				if (!res.ok) {
					const body = (await res.json().catch(() => ({}))) as {
						detail?: string;
					};
					throw new Error(body.detail ?? `login failed (${res.status})`);
				}
				const { access_token } = (await res.json()) as {
					access_token: string;
				};
				accept(access_token);
			} catch (e) {
				setError(e instanceof Error ? e.message : "login failed");
			}
		},
		[accept],
	);

	const logout = useCallback(async () => {
		accept(null);
		if (userManager) await userManager.signoutRedirect().catch(() => undefined);
	}, [accept]);

	const value = useMemo<AuthState>(() => {
		let identity: Identity | null = null;
		if (token) {
			try {
				identity = identityOf(token);
			} catch {
				identity = null;
			}
		}
		return { identity, token, ready, login, loginWithPassword, logout, error };
	}, [token, ready, login, loginWithPassword, logout, error]);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
	const ctx = useContext(AuthContext);
	if (!ctx) throw new Error("useAuth outside AuthProvider");
	return ctx;
}
