import { defineConfig } from "vite";

declare const process: { env: Record<string, string | undefined> };

// No React plugin: esbuild compiles TSX with the automatic JSX runtime.
// The /api proxy forwards to the FastAPI backend, so the browser never makes a cross-origin call.
export default defineConfig({
  esbuild: { jsx: "automatic" },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.AGNT911_API_URL ?? "http://127.0.0.1:8000", // override to point at another backend
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
