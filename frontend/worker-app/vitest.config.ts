import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * This app's screens are React, and most of what is worth pinning here is behaviour a worker
 * performs with a keyboard or a tap — sign-in, sign-out, the loading/empty/error states of the
 * task-list shell — so the suite runs in a DOM, the same shape `@opsgrids/ui` and
 * `@opsgrids/client` already use.
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
