/**
 * Real app screens from their production builds, signed in through the session's own reload path
 * and fed by contract-validated mocks (`fixtures/api/`), in both themes at every QA width.
 */

import { expect, test } from "@playwright/test";

import { RULE_TAGS, scanA11y } from "../lib/a11y";
import { mockApi } from "../lib/api";
import { a11yKey, snapshotName } from "../lib/catalogue";
import { THEMES, VIEWPORT_HEIGHT, WIDTHS } from "../lib/env";
import { isolate } from "../lib/network";
import { PAGES } from "../lib/pages";
import { signIn } from "../lib/session";
import { settle } from "../lib/stabilise";

for (const spec of PAGES) {
  test.describe(spec.surface, () => {
    for (const theme of THEMES) {
      // A fresh context per theme: the theme is read from storage before the app's first paint,
      // so it is set by an init script, not switched on a live page.
      test(theme, async ({ page }, testInfo) => {
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        const network = await isolate(page, [spec.origin]);
        const api = await mockApi(page, spec.origin, spec.routes);
        await signIn(page, spec.session, spec.token, theme);

        for (const width of WIDTHS) {
          await test.step(`${width}`, async () => {
            await page.setViewportSize({ width, height: VIEWPORT_HEIGHT[width] });
            await page.goto(`${spec.origin}${spec.path}`);
            await settle(page);
            if (spec.act) {
              await spec.act(page);
              await settle(page);
            }
            await expect(spec.ready(page)).toBeVisible();

            await expect
              .soft(page)
              .toHaveScreenshot(snapshotName(spec.surface, theme, width), { fullPage: spec.capture === "fullpage" });

            const regressions = await scanA11y(page, testInfo, a11yKey(spec.surface, theme, width), RULE_TAGS.page);
            expect.soft(regressions, "new accessibility violations").toEqual([]);
          });
        }

        expect(errors, "uncaught errors while rendering").toEqual([]);
        expect(api.unmocked, "API calls with no fixture").toEqual([]);
        expect(network.unexpected, "requests outside the app").toEqual([]);
      });
    }
  });
}
