import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * What is worth pinning in the operator console is behaviour a person performs — signing in with
 * a second factor, reading a lockout, opening an override — and the one boundary that must never
 * leak: an operator token and a tenant token never reach each other's routes. All of it is DOM
 * and module behaviour, so the suite runs in jsdom, the same shape `@opsgrids/ui` and the product
 * apps use.
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
    // A route is a lazy chunk, so the first visit in a file pays to load it, and CI shares its
    // machines. A generous ceiling costs nothing when everything is fast and turns a slow run into
    // a slow pass instead of a flaky failure; a test that is actually wrong still fails, just later.
    testTimeout: 20_000,
  },
});
