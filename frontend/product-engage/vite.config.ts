import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { assetsInlineLimit } from "@opsgrids/config/vite";

// One Vite app per product (ADR-0001). Dev on 4420 — clear of every other app in this workspace
// (4310–4400 and the internal console on 4500), so two products can run side by side.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 4420,
    strictPort: true,
    // Same-origin API: the browser calls `/api/*`, which Vite forwards to the gateway on 8100.
    // This sidesteps CORS entirely — no gateway restart, no cross-origin preflight. It is the
    // same arrangement Caddy runs in production.
    proxy: {
      "/api": {
        target: "http://localhost:8100",
        changeOrigin: true,
      },
    },
  },
  build: {
    assetsInlineLimit,
  },
});
