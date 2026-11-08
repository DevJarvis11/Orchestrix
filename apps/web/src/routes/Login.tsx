import { useState } from "react";
import { Button, Card, ErrorNote, Field, Textarea } from "../components/ui";
import { AUTH_MODE, useAuth } from "../lib/auth";

export function Login() {
	const { login, error } = useAuth();
	const [token, setToken] = useState("");
	return (
		<main className="grid min-h-screen place-items-center p-4">
			<Card title="Sign in to Orchestrix" className="w-full max-w-lg">
				{AUTH_MODE === "oidc" ? (
					<div className="space-y-3">
						<p className="text-sm text-muted">
							You will be redirected to the organisation's single sign-on.
						</p>
						<Button variant="primary" onClick={() => void login()}>
							Continue with SSO
						</Button>
					</div>
				) : (
					<form
						className="space-y-3"
						onSubmit={(e) => {
							e.preventDefault();
							void login(token);
						}}
					>
						<p className="text-sm text-muted">
							Development mode: paste a token from{" "}
							<code className="font-mono">make dev-token</code>. It is kept in
							memory only.
						</p>
						<Field label="Dev token">
							{(id) => (
								<Textarea
									id={id}
									rows={5}
									className="font-mono text-xs"
									value={token}
									onChange={(e) => setToken(e.target.value)}
									required
								/>
							)}
						</Field>
						<ErrorNote error={error} />
						<Button type="submit" variant="primary">
							Sign in
						</Button>
					</form>
				)}
			</Card>
		</main>
	);
}
