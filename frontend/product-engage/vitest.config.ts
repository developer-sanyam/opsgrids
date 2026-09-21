import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * What is worth pinning in this app is the sending identity itself: that no call and no fixture
 * ever carries the provider credential, and that a sender the service has not made usable is not
 * rendered as one. Those are DOM and module behaviours, so the suite runs in jsdom — the same
 * shape `@opsgrids/ui` and the other product apps already use.
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
