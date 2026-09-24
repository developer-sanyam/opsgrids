/**
 * Loading one story, in one theme, in the workshop's static build — and knowing when it is done.
 */

import type { Locator, Page } from "@playwright/test";

import { ORIGIN } from "./env";
import type { Theme } from "./env";
import type { Story } from "./catalogue";

/**
 * What a screenshot of a story covers, chosen by the story's tags:
 *
 * - `viewport` — exactly the viewport. For overlays (a dialog, the mobile drawer, a toast), which
 *   are positioned against the viewport and are only ever seen inside it.
 * - `fullpage` — the whole document. For stories that are a page's frame (the app shell).
 * - neither — the story's root element: its own content and nothing else, which keeps the
 *   baseline small and the diff about the component.
 */
export const VIEWPORT_TAG = "viewport";
export const FULLPAGE_TAG = "fullpage";

export function storyUrl(story: Story, theme: Theme): string {
  const query = new URLSearchParams({ id: story.id, viewMode: "story" });
  return `${ORIGIN.storybook}/iframe.html?${query.toString()}&globals=theme:${theme}`;
}

/**
 * Wait for Storybook's own render lifecycle to reach `finished` — past rendering, past any `play`
 * function (the interactions that open a menu or type into a combobox), past the preview's own
 * wait for finite animations — then fail if it finished by showing an error instead of the story.
 */
export async function openStory(page: Page, story: Story, theme: Theme): Promise<void> {
  await page.goto(storyUrl(story, theme));
  await page.waitForFunction(
    (id) => {
      const preview = (window as unknown as {
        __STORYBOOK_PREVIEW__?: { storyRenders?: { id: string; phase?: string }[] };
      }).__STORYBOOK_PREVIEW__;
      return preview?.storyRenders?.some((render) => render.id === id && render.phase === "finished") ?? false;
    },
    story.id,
    { timeout: 15_000 },
  );
  const failed = await page.evaluate(
    () =>
      document.body.classList.contains("sb-show-errordisplay") ||
      document.body.classList.contains("sb-show-nopreview"),
  );
  if (failed) {
    const message = await page.locator("#error-message").textContent();
    throw new Error(`Story ${story.id} rendered an error: ${message ?? "(no message)"}`);
  }
}

/** The thing to photograph, and whether Playwright should extend it to the full document. */
export function captureTarget(page: Page, story: Story): { target: Page | Locator; fullPage: boolean } {
  if (story.tags.includes(VIEWPORT_TAG)) return { target: page, fullPage: false };
  if (story.tags.includes(FULLPAGE_TAG)) return { target: page, fullPage: true };
  return { target: page.locator("#storybook-root"), fullPage: false };
}
