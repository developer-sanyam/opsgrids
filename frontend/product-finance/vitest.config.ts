import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * Render-level form tests need a DOM; the pure data/lib suites don't but share the environment
 * for one config rather than per-file setup.
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
