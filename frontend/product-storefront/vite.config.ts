import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// One Vite app per product (ADR-0001). Dev on 4400 — clear of the marketing site on 4300 and of
// every product app below it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 4400,
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
});
