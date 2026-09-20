import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * jsdom, the same shape every other app in the workspace uses. What is worth pinning here is
 * behaviour a stranger meets first: the sign-up form's refusals and the marketplace's rendering
 * of the catalogue and the organisation's subscriptions from the wire shapes alone.
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
