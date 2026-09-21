import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * The client's mappers and request shapes, the pure rollup functions in `data/derive.ts`, the
 * money/date parsing helpers and the form dialogs all live here. The form dialogs render into the
 * DOM, so the suite runs in `jsdom` — the same shape `@opsgrids/ui` and the other product apps
 * already use — rather than splitting into two environments for one app.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
    restoreMocks: true,
  },
});
