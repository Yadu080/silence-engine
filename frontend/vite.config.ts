import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the Flask backend so the browser stays same-origin.
const backend = process.env.SILENCE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  // Relative asset URLs: the build works at "/", under Jupyter's "/proxy/8000/" or any proxy prefix.
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: { "/api": { target: backend, changeOrigin: false } },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
