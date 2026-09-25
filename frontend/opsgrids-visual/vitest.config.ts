import { defineConfig } from "vitest/config";

/**
 * The harness's own unit tests — the a11y ratchet's arithmetic, the fixture contracts, the font
 * stub. Pure Node, no browser: `pnpm -r test` runs these; the screenshots run under
 * `pnpm visual:check`.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts", "fixtures/**/*.test.ts"],
  },
});
