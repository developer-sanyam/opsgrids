/**
 * axe, run on the same rendering the screenshot was taken of, and judged against
 * `a11y-baseline.json` (see `a11yBaseline.ts` for the rule).
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";

import { compare, countByRule } from "./a11yBaseline";
import type { Baseline, RuleCounts } from "./a11yBaseline";
import { HARNESS_ROOT, UPDATING } from "./env";

/**
 * WCAG 2.0, 2.1 and 2.2 at levels A and AA — the bar `frontend/CLAUDE.md` sets. 2.2 AA brings
 * `target-size`, the brief's touch-target rule (§20).
 */
const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/**
 * A story is a fragment, so landmark and heading-structure rules ("all content in a landmark",
 * "one h1") would flag every one of them for a page it is not. A real page is held to those too.
 */
export const RULE_TAGS = {
  story: WCAG,
  page: [...WCAG, "best-practice"],
} as const;

export const BASELINE_FILE = join(HARNESS_ROOT, "a11y-baseline.json");

/** Where an update run leaves each rendering's counts for the global teardown to merge. */
export const RECORD_DIR = join(HARNESS_ROOT, "test-results", "a11y-records");

let cached: Baseline | null = null;

export function readBaseline(): Baseline {
  if (cached === null) {
    cached = existsSync(BASELINE_FILE)
      ? (JSON.parse(readFileSync(BASELINE_FILE, "utf8")) as Baseline)
      : {};
  }
  return cached;
}

export interface A11yRecord {
  key: string;
  counts: RuleCounts;
}

/**
 * Run axe over the page and hold it to the baseline. Returns one sentence per rule that got
 * worse — empty when the rendering is no worse than its baseline. In an update run the counts are
 * recorded instead and nothing is reported; the teardown rewrites the baseline from the records.
 */
export async function scanA11y(
  page: Page,
  testInfo: TestInfo,
  key: string,
  tags: readonly string[],
): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags([...tags]).analyze();
  const counts = countByRule(results.violations);

  if (results.violations.length > 0) {
    await testInfo.attach(`axe ${key}`, {
      body: JSON.stringify(results.violations, null, 2),
      contentType: "application/json",
    });
  }

  if (UPDATING) {
    mkdirSync(RECORD_DIR, { recursive: true });
    const file = createHash("sha1").update(key).digest("hex");
    const record: A11yRecord = { key, counts };
    writeFileSync(join(RECORD_DIR, `${file}.json`), JSON.stringify(record));
    return [];
  }

  const { regressions, improvements } = compare(counts, readBaseline()[key] ?? {});
  for (const better of improvements) {
    testInfo.annotations.push({
      type: "a11y-improved",
      description: `${key} ${better.rule}: ${better.allowed} → ${better.found}. Run \`pnpm visual:update\` to lower the baseline.`,
    });
  }
  return regressions.map((regression) => {
    const violation = results.violations.find((candidate) => candidate.id === regression.rule);
    const targets = (violation?.nodes ?? [])
      .slice(0, 5)
      .map((node) => `\n      ${node.target.join(" ")}`)
      .join("");
    const help = violation ? ` — ${violation.help} (${violation.helpUrl})` : "";
    return `${key} ${regression.rule}: ${regression.found} node(s), baseline allows ${regression.allowed}${help}${targets}`;
  });
}
