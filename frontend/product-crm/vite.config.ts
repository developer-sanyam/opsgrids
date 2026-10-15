import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import { assetsInlineLimit } from "@opsgrids/config/vite";

// One Vite app per product (ADR-0001). Dev on 4340 so it never collides with the marketing
// site on 4300 or the HRMS product on 4310.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 4340,
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
