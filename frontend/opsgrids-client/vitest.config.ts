import { defineConfig } from "vitest/config";

/**
 * The client's hooks are React, so its tests need a DOM. The scope ladder underneath them is
 * pure and is pinned directly — a permission check that quietly widens is the one bug in this
 * package that no screen would reveal.
 */
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
    restoreMocks: true,
  },
});
