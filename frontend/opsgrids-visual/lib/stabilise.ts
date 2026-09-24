/**
 * "Settled" means: every web font the page asked for has loaded, nothing is still loading data,
 * and two frames have painted since. Screenshots are taken only then — never after a sleep.
 */

import type { Page } from "@playwright/test";

/** Loading placeholders the design system draws (`Skeleton`, and the auth gate's pulsing mark). */
const LOADING = ".animate-pulse, [aria-busy='true']";

interface SettleOptions {
  /**
   * Wait for the network to go quiet. An app page fetches its data after the first render; a story
   * has none to fetch, and Storybook's own lifecycle has already said it is done — the half-second
   * of quiet this costs would be most of a story's run time for nothing.
   */
  network?: boolean;
  /** Wait for loading placeholders to go. Off for stories, whose loading state may be the point. */
  loading?: boolean;
}

export async function settle(page: Page, { network = true, loading = true }: SettleOptions = {}): Promise<void> {
  if (network) await page.waitForLoadState("networkidle");
  if (loading) {
    await page.waitForFunction((selector) => document.querySelectorAll(selector).length === 0, LOADING, {
      timeout: 15_000,
    });
  }
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
}
