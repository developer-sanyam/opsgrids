import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * `DirectoryProvider` and the components that resolve ids through it are React, so the suite
 * runs in a DOM — the same shape `@opsgrids/ui` and `@opsgrids/client` already use. The mapper
 * and client tests below don't need a DOM at all, but one environment for the whole app is one
 * fewer thing to configure per file.
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
