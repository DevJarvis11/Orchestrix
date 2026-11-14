import { KeyRound, ShieldCheck } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { LogoMark } from "../components/Logo";
import { Button, ErrorNote, Field, Input } from "../components/ui";
import { AUTH_MODE, useAuth } from "../lib/auth";

export function Login() {
	const { login, loginWithPassword, error } = useAuth();
	const [username, setUsername] = useState("");
	const [password, setPassword] = useState("");
	return (
		<main className="relative grid min-h-screen place-items-center overflow-hidden px-4 py-10">
			<div
				aria-hidden
				className="pointer-events-none absolute -top-40 left-1/2 h-[36rem] w-[min(70rem,140vw)] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,var(--glow),transparent)]"
			/>
			<motion.div
				initial={{ opacity: 0, y: 16 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
				className="relative w-full max-w-md"
			>
				<div className="mb-8 text-center">
					<LogoMark className="mx-auto size-14 drop-shadow-lg" />
					<h1 className="mt-5 text-[28px] font-bold tracking-tight">
						Sign in to Orchestrix
					</h1>
					<p className="mt-1.5 text-[15px] text-muted">
						Plan, run and approve multi-agent work.
					</p>
				</div>
				<div className="panel p-6 shadow-pop">
					{AUTH_MODE === "oidc" ? (
						<div className="space-y-4 text-center">
							<p className="text-sm text-muted">
								You will be redirected to your organisation's single sign-on.
							</p>
							<Button
								variant="primary"
								className="h-11 w-full text-[15px]"
								onClick={() => void login()}
							>
								<ShieldCheck aria-hidden className="size-4" />
								Continue with SSO
							</Button>
						</div>
					) : (
						<form
							className="space-y-4"
							onSubmit={(e) => {
								e.preventDefault();
								void loginWithPassword(username, password);
							}}
						>
							<p className="text-sm text-muted">
								Development mode: sign in with a demo account (admin, alice or
								bob).
							</p>
							<Field label="Username">
								{(id) => (
									<Input
										id={id}
										autoComplete="username"
										value={username}
										onChange={(e) => setUsername(e.target.value)}
										required
									/>
								)}
							</Field>
							<Field label="Password">
								{(id) => (
									<Input
										id={id}
										type="password"
										autoComplete="current-password"
										value={password}
										onChange={(e) => setPassword(e.target.value)}
										required
									/>
								)}
							</Field>
							<ErrorNote error={error} />
							<Button
								type="submit"
								variant="primary"
								className="h-11 w-full text-[15px]"
							>
								<KeyRound aria-hidden className="size-4" />
								Sign in
							</Button>
						</form>
					)}
				</div>
			</motion.div>
		</main>
	);
}
