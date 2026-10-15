import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { assetsInlineLimit } from "@opsgrids/config/vite";

// One Vite app per surface (ADR-0001; the worker PWA's own boundary is ADR-0024 §7). Dev on 4440,
// clear of every other app's port (product-support is the highest at 4430).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 4440,
    strictPort: true,
    // Same-origin API: the browser calls `/api/*`, which Vite forwards to the gateway on 8100.
    // This sidesteps CORS entirely — no gateway restart, no cross-origin preflight.
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
