import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

// The scheduling tests assert India Standard Time wall clocks and that the browser's own zone is
// already IST ("this sandbox already runs on IST"), which was true of every machine they were
// written on and false of CI's UTC runners — five failures there, none on a laptop. A test that
// depends on the zone of the machine running it is pinned to the zone it asserts instead. Set here,
// before vitest starts its workers, so every one of them inherits it.
process.env.TZ = "Asia/Kolkata";

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
