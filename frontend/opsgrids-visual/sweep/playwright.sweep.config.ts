import { defineConfig } from "@playwright/test";

/**
 * The layout sweep (documented in `frontend/VISUAL-TESTING.md`). Deliberately separate from
 * `playwright.config.ts`: it starts a dev server per app, so it is a developer's tool and is never
 * part of `pnpm visual:check` or CI.
 */
export default defineConfig({
  testDir: ".",
  testMatch: "ui-sweep.spec.ts",
  outputDir: "../test-results/sweep-runtime",
  timeout: 40 * 60_000,
  fullyParallel: true,
  workers: Number(process.env["SWEEP_WORKERS"] ?? 4),
  retries: 0,
  reporter: [["list"]],
  globalTeardown: "./merge-report.ts",
  use: { browserName: "chromium", headless: true, deviceScaleFactor: 1, locale: "en-IN", timezoneId: "Asia/Kolkata" },
});
