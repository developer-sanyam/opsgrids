/**
 * A page under test talks to the local static server it was loaded from, to the two Google Fonts
 * hosts (answered locally, see `fonts.ts`), and to nothing else. Anything else is recorded and
 * refused, and the test fails on it: a request the harness did not expect is an input the baseline
 * does not control.
 */

import type { Page } from "@playwright/test";

import { FIXED_NOW } from "./env";
import { fontFile, stylesheetFor } from "./fonts";

export interface Isolation {
  /** Requests that were refused — every one of them is a failure the test reports. */
  unexpected: string[];
}

export async function isolate(page: Page, allowedOrigins: readonly string[]): Promise<Isolation> {
  const unexpected: string[] = [];
  const context = page.context();

  // Registered first, so it is consulted last: Playwright tries route handlers newest-first.
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (allowedOrigins.includes(url.origin)) {
      await route.continue();
      return;
    }
    unexpected.push(`${route.request().method()} ${url.href}`);
    await route.abort("blockedbyclient");
  });

  await context.route("https://fonts.googleapis.com/**", async (route) => {
    const body = stylesheetFor(new URL(route.request().url()));
    await route.fulfill({ status: 200, contentType: "text/css; charset=utf-8", body });
  });

  await context.route("https://fonts.gstatic.com/**", async (route) => {
    const body = fontFile(new URL(route.request().url()));
    await route.fulfill({
      status: 200,
      contentType: "font/woff2",
      headers: { "access-control-allow-origin": "*" },
      body,
    });
  });

  // "Now" is frozen; timers keep running, so debounces and fetches behave normally.
  await page.clock.setFixedTime(new Date(FIXED_NOW));

  return { unexpected };
}
