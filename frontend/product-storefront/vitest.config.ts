import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * The renderer is what is worth pinning here: every section type has to come out of data alone, an
 * unknown type has to degrade instead of throwing, and a store has to be a genuine fork of its
 * theme rather than an alias of it. All of that is DOM behaviour, so the suite runs in jsdom — the
 * same shape `@opsgrids/ui` and the other product apps already use.
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
