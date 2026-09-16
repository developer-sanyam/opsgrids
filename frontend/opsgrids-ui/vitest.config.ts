import { defineConfig } from "vitest/config";

/**
 * The design system's tests run in a DOM, because most of what is worth pinning here is
 * behaviour a user performs with a keyboard — focus trapping, combobox arrow keys, a live
 * region's text — and none of that is observable from a pure render. The pure helpers
 * (the encoder, the zone formatters) run in the same environment quite happily.
 */
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
    restoreMocks: true,
  },
});
