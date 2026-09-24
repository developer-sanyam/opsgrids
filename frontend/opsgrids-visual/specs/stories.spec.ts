/**
 * Every story in the workshop, in both themes, at every QA width: one screenshot and one axe run
 * per rendering. The list comes from the workshop build's own index, so a new story is covered
 * the moment it exists — and has no baseline until `pnpm visual:update` gives it one, which fails
 * `visual:check` until somebody has looked at it.
 *
 * One test per story, six renderings inside it on one page: the workshop bundle is fetched once
 * per story instead of once per rendering, and every rendering still reports on its own (soft
 * assertions), so one bad width does not hide the other five.
 */

import { expect, test } from "@playwright/test";

import { RULE_TAGS, scanA11y } from "../lib/a11y";
import { a11yKey, loadStories, snapshotName, storySurface } from "../lib/catalogue";
import { ORIGIN, THEMES, VIEWPORT_HEIGHT, WIDTHS } from "../lib/env";
import { isolate } from "../lib/network";
import { settle } from "../lib/stabilise";
import { captureTarget, openStory } from "../lib/storybook";

for (const story of loadStories()) {
  const surface = storySurface(story);

  test(story.id, async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const network = await isolate(page, [ORIGIN.storybook]);

    for (const theme of THEMES) {
      for (const width of WIDTHS) {
        await test.step(`${theme} ${width}`, async () => {
          await page.setViewportSize({ width, height: VIEWPORT_HEIGHT[width] });
          await openStory(page, story, theme);
          // A story's loading state is the point of the story — a skeleton is not "still loading".
          await settle(page, { network: false, loading: false });

          const { target, fullPage } = captureTarget(page, story);
          await expect.soft(target).toHaveScreenshot(snapshotName(surface, theme, width), { fullPage });

          const regressions = await scanA11y(page, testInfo, a11yKey(surface, theme, width), RULE_TAGS.story);
          expect.soft(regressions, "new accessibility violations").toEqual([]);
        });
      }
    }

    expect(errors, "uncaught errors while rendering").toEqual([]);
    expect(network.unexpected, "requests outside the workshop").toEqual([]);
  });
}
