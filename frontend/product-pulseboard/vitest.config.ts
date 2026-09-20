import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * What's under test here is the API client's mappers and request shapes, and the pure rollup
 * functions in `data/derive.ts` — none of it touches the DOM, so a plain Node environment is
 * enough (no `jsdom` dependency to carry for a suite that never renders anything).
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
