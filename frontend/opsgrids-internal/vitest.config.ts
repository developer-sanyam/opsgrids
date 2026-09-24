import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * The console's own suite is small and DOM-heavy (dialogs, tables, permission gating), so it runs
 * in jsdom — the same shape `@opsgrids/client` and the product apps already use.
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
    // `client.ts`'s `USE_FIXTURES` is `import.meta.env.DEV && VITE_USE_FIXTURES !== "false"` —
    // opt-*out* of fixtures, unlike the product apps — and it is read once at module load, before
    // any per-test `vi.stubEnv` could reach it. Pinning it here means the suite exercises the real
    // live-fetch branch by default, the same branch a production build takes.
    env: { VITE_USE_FIXTURES: "false" },
  },
});
