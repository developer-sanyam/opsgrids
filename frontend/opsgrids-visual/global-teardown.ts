/**
 * After every run: bring `a11y-baseline.json` up to date (update runs), and make sure no baseline
 * PNG outlives the story or page it was taken of (update runs delete them, check runs fail on them).
 */

import { existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { BASELINE_FILE, readBaseline, RECORD_DIR } from "./lib/a11y";
import type { A11yRecord } from "./lib/a11y";
import { merge, serialise } from "./lib/a11yBaseline";
import type { Baseline } from "./lib/a11yBaseline";
import { a11yKey, allRenderings, snapshotName } from "./lib/catalogue";
import { HARNESS_ROOT, UPDATING } from "./lib/env";

const PLATFORM_DIR = join(HARNESS_ROOT, "baselines", process.platform);

function filesUnder(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

function pruneEmptyDirs(dir: string): void {
  if (!existsSync(dir)) return;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) pruneEmptyDirs(join(dir, entry.name));
  }
  if (readdirSync(dir).length === 0) rmSync(dir, { recursive: true });
}

export default function globalTeardown(): void {
  const renderings = allRenderings();

  if (UPDATING && existsSync(RECORD_DIR)) {
    const results: Baseline = {};
    for (const file of readdirSync(RECORD_DIR)) {
      const record = JSON.parse(readFileSync(join(RECORD_DIR, file), "utf8")) as A11yRecord;
      results[record.key] = record.counts;
    }
    const known = new Set(renderings.map(({ surface, theme, width }) => a11yKey(surface, theme, width)));
    writeFileSync(BASELINE_FILE, serialise(merge(readBaseline(), results, known)));
  }

  const expected = new Set(
    renderings.map(({ surface, theme, width }) => snapshotName(surface, theme, width).join(sep)),
  );
  const orphans = filesUnder(PLATFORM_DIR).filter((file) => !expected.has(relative(PLATFORM_DIR, file)));
  if (orphans.length === 0) return;

  if (UPDATING) {
    for (const file of orphans) rmSync(file);
    pruneEmptyDirs(PLATFORM_DIR);
    console.log(`Removed ${orphans.length} baseline(s) whose story or page no longer exists.`);
    return;
  }
  throw new Error(
    `${orphans.length} baseline(s) belong to no story or page any more — run \`pnpm visual:update\` ` +
      `to remove them:\n${orphans.map((file) => `  ${relative(HARNESS_ROOT, file)}`).join("\n")}`,
  );
}
