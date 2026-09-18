import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// The operator console (ADR-0001 `opsgrids-internal`). Dev on 4330 so it never collides with the
// marketing site (4300) or the product apps (4310+).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 4330,
    strictPort: true,
    // Same-origin API: the browser calls `/api/*`, which Vite forwards to admin-gateway on 8110.
    // Operator traffic never touches app-gateway (ADR-0003 §2) — a product token is rejected here
    // at signature-verification time. This sidesteps CORS entirely in dev.
    proxy: {
      "/api": {
        target: "http://localhost:8110",
        changeOrigin: true,
      },
    },
  },
});
