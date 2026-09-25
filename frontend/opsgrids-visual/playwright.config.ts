import { defineConfig } from "@playwright/test";

import { LOCALE, ORIGIN, PORTS, TIMEZONE, UPDATING } from "./lib/env";

/** Each app's package directory, relative to this one. */
const APP_DIR = {
  dashboard: "../opsgrids-dashboard",
  crm: "../product-crm",
  internal: "../opsgrids-internal",
} as const;

/**
 * The visual and accessibility regression harness. `frontend/VISUAL-TESTING.md` is the manual;
 * this file holds the knobs that make a screenshot comparable with one taken yesterday.
 *
 * Run through `pnpm visual:check` / `pnpm visual:update`, which build the workshop and the three
 * apps first — the servers below serve those builds, they do not build anything.
 */
export default defineConfig({
  testDir: "specs",
  outputDir: "test-results",
  snapshotDir: "baselines",
  // One tree per OS: font rasterisation differs between macOS and Linux, so a baseline is only
  // comparable with a run on the platform that took it. See VISUAL-TESTING.md, "Platforms".
  snapshotPathTemplate: "{snapshotDir}/{platform}/{arg}{ext}",
  globalTeardown: "./global-teardown.ts",
  fullyParallel: true,
  forbidOnly: true,
  // One test is one story or page in six renderings; each rendering is a navigation, a
  // screenshot and an axe run.
  timeout: 120_000,
  retries: 0,
  workers: process.env["VISUAL_WORKERS"] ?? "75%",
  // A check run never writes a baseline, even a missing one: a new story fails until someone runs
  // `pnpm visual:update` and looks at what it produced.
  updateSnapshots: UPDATING ? "changed" : "none",
  reporter: [["dot"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
      // Per-pixel colour distance, 0–1. Playwright's default (0.2) lets a text colour move by
      // ~15% luminance unnoticed — exactly the size of change a token re-value makes — so this is
      // tighter. Rendering on one machine is bit-for-bit repeatable, so tight costs nothing.
      threshold: 0.05,
      // A handful of anti-aliased pixels, never a changed control.
      maxDiffPixels: 20,
    },
  },
  use: {
    browserName: "chromium",
    headless: true,
    deviceScaleFactor: 1,
    locale: LOCALE,
    timezoneId: TIMEZONE,
    // The palette comes from `data-theme`, never from the OS preference, so this is fixed.
    colorScheme: "light",
    // console.css turns off the page-load rise under reduced motion; the screenshot's own
    // `animations: "disabled"` then settles anything else.
    reducedMotion: "reduce",
    serviceWorkers: "block",
    trace: "retain-on-failure",
  },
  // Static servers over what `build:targets` produced. Never reused: a server left over from
  // another run would serve that run's build.
  webServer: [
    {
      name: "workshop",
      command: `pnpm --dir ../opsgrids-ui exec vite preview --outDir storybook-static --port ${PORTS.storybook} --strictPort`,
      url: `${ORIGIN.storybook}/iframe.html`,
      reuseExistingServer: false,
    },
    ...(["dashboard", "crm", "internal"] as const).map((app) => ({
      name: app,
      command: `pnpm --dir ${APP_DIR[app]} exec vite preview --port ${PORTS[app]} --strictPort`,
      url: ORIGIN[app],
      reuseExistingServer: false,
    })),
  ],
});
