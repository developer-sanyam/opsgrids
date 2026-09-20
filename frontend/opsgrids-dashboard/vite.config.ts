import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

/**
 * One entry point, one output. The dashboard is served by Caddy on `app.opsgrids.com` from
 * `dist/` (ADR-0013 §4), and calls the API same-origin at `/api/*` — Caddy proxies that to the
 * gateway on every host it serves, so no CORS surface exists in production and none is faked here.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 4500,
    strictPort: true,
    // Same-origin API in dev too: the browser calls `/api/*`, Vite forwards it to the gateway.
    proxy: {
      "/api": {
        target: "http://localhost:8100",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
