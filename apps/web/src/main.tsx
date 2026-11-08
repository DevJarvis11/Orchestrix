import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { Spinner } from "./components/ui";
import { ApiError } from "./lib/api/client";
import { AuthProvider, useAuth } from "./lib/auth";
import { router } from "./router";
import { Login } from "./routes/Login";

const queryClient = new QueryClient({
	defaultOptions: {
		queries: {
			staleTime: 10_000,
			retry: (count, error) =>
				!(error instanceof ApiError && error.status < 500) && count < 2,
		},
	},
});

function App() {
	const { identity, ready } = useAuth();
	if (!ready) return <Spinner label="Signing in" />;
	if (!identity) return <Login />;
	return <RouterProvider router={router} />;
}

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");

createRoot(root).render(
	<StrictMode>
		<QueryClientProvider client={queryClient}>
			<AuthProvider>
				<App />
			</AuthProvider>
		</QueryClientProvider>
	</StrictMode>,
);
