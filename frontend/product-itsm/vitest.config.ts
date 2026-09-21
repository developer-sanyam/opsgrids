import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * One environment for the whole app: the client tests below don't need a DOM, but the form
 * render tests do, and jsdom is the same shape `@opsgrids/ui` and `@opsgrids/client` already use.
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
