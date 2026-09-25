/**
 * Everything the harness holds fixed, in one place. A screenshot is only a regression test if the
 * inputs that are not the code under test cannot move: the viewport, the clock, the zone and
 * locale, the fonts, the motion, and the network.
 */

import { fileURLToPath } from "node:url";

/** `frontend/opsgrids-visual/`, wherever the run was started from. */
export const HARNESS_ROOT = fileURLToPath(new URL("..", import.meta.url));

/** The owner brief's QA widths (§7): phone, tablet, desktop. */
export const WIDTHS = [375, 768, 1280] as const;
export type Width = (typeof WIDTHS)[number];

/**
 * The viewport height at each width — a real device's, so a full-screen overlay (a dialog, the
 * mobile drawer, a toast) is photographed in the space it actually gets.
 */
export const VIEWPORT_HEIGHT: Record<Width, number> = { 375: 812, 768: 1024, 1280: 800 };

export const THEMES = ["light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

/**
 * The instant `Date.now()` returns in every page. Timers still run (Playwright's
 * `clock.setFixedTime`), so debounces and fetches behave; only "now" is frozen, which is what
 * relative times ("3 days ago") and "this month" boundaries read. Fixture timestamps are written
 * relative to it.
 */
export const FIXED_NOW = "2026-09-15T10:30:00+05:30";

export const LOCALE = "en-IN";
export const TIMEZONE = "Asia/Kolkata";

/**
 * The first of the four ports the static servers take. `VISUAL_PORT_BASE` moves the block, so two
 * checkouts (two worktrees, two branches) can run the harness at the same time: the servers are
 * never reused (`playwright.config.ts`), so a second run on the same ports refuses to start rather
 * than photograph the other checkout's build.
 */
function portBase(): number {
  const raw = process.env["VISUAL_PORT_BASE"];
  if (raw === undefined || raw === "") return 4910;
  const base = Number(raw);
  if (!Number.isInteger(base) || base < 1024 || base > 65532) {
    throw new Error(`VISUAL_PORT_BASE must be an integer port between 1024 and 65532, got "${raw}"`);
  }
  return base;
}

const BASE = portBase();

/**
 * Local static servers, one per target. Clear of every app's dev/preview port (43x0, 4500) and of
 * Storybook's dev server (6006), so the harness runs beside a developer's own servers.
 */
export const PORTS = {
  storybook: BASE,
  dashboard: BASE + 1,
  crm: BASE + 2,
  internal: BASE + 3,
} as const;

export const ORIGIN = {
  storybook: `http://localhost:${PORTS.storybook}`,
  dashboard: `http://localhost:${PORTS.dashboard}`,
  crm: `http://localhost:${PORTS.crm}`,
  internal: `http://localhost:${PORTS.internal}`,
} as const;

/** `pnpm visual:update` sets this: baselines are rewritten rather than compared. */
export const UPDATING = process.env["VISUAL_UPDATE"] === "1";
