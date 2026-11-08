import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// https://vite.dev/config/
export default defineConfig({
	plugins: [react(), tailwindcss()],
	server: {
		port: 5173,
		strictPort: true,
		// With VITE_API_URL="" the browser calls the API on this origin and Vite forwards it.
		// Needed when the browser runs on Windows and WSL does not forward localhost.
		proxy: {
			"/v1": "http://127.0.0.1:58100",
			"/healthz": "http://127.0.0.1:58100",
		},
	},
	build: { chunkSizeWarningLimit: 2000 },
	test: {
		environment: "jsdom",
		globals: true,
		setupFiles: ["./src/test/setup.ts"],
	},
});
