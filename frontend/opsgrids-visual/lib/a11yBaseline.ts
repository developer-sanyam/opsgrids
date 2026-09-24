/**
 * The accessibility gate is "no new violations", not "zero today".
 *
 * `a11y-baseline.json` records, for every rendering that has any (surface × theme × width), how
 * many nodes each axe rule flagged when the baseline was taken. A run fails when any rule flags
 * more nodes than that on any rendering — including a rule that was at zero. Fewer is an
 * improvement: it passes, says so, and `pnpm visual:update` ratchets the number down so it cannot
 * creep back up.
 *
 * Counts per rule rather than axe's CSS selectors, deliberately: the redesign rewrites markup
 * wholesale, and a selector-keyed list would report every existing problem as "new" the first time
 * a wrapper `div` moved. A count says what the gate needs — did this screen get worse.
 *
 * Pure functions only, so the arithmetic the gate rests on is unit-tested (`a11yBaseline.test.ts`).
 */

/** axe rule id → number of offending nodes. */
export type RuleCounts = Record<string, number>;

/** `a11yKey(...)` → the rule counts recorded for that rendering. Empty renderings are omitted. */
export type Baseline = Record<string, RuleCounts>;

export interface RuleDelta {
  rule: string;
  allowed: number;
  found: number;
}

/** The shape of axe's `violations`, as much of it as the count needs. */
export function countByRule(violations: readonly { id: string; nodes: readonly unknown[] }[]): RuleCounts {
  const counts: RuleCounts = {};
  for (const violation of violations) {
    counts[violation.id] = (counts[violation.id] ?? 0) + violation.nodes.length;
  }
  return counts;
}

/** Every rule that got worse, and every rule that got better, against what is allowed. */
export function compare(
  found: RuleCounts,
  allowed: RuleCounts,
): { regressions: RuleDelta[]; improvements: RuleDelta[] } {
  const regressions: RuleDelta[] = [];
  const improvements: RuleDelta[] = [];
  const rules = new Set([...Object.keys(found), ...Object.keys(allowed)]);
  for (const rule of [...rules].sort()) {
    const now = found[rule] ?? 0;
    const was = allowed[rule] ?? 0;
    if (now > was) regressions.push({ rule, allowed: was, found: now });
    else if (now < was) improvements.push({ rule, allowed: was, found: now });
  }
  return { regressions, improvements };
}

/**
 * The baseline after an update run.
 *
 * `results` holds only the renderings that ran, so a filtered run (`--grep`) rewrites exactly what
 * it measured and leaves everything else alone. `known` is every rendering that still exists: an
 * entry outside it belongs to a story or page that was removed, and is dropped.
 */
export function merge(baseline: Baseline, results: Baseline, known: ReadonlySet<string>): Baseline {
  const next: Baseline = {};
  for (const [key, counts] of Object.entries(baseline)) {
    if (known.has(key) && !(key in results)) next[key] = counts;
  }
  for (const [key, counts] of Object.entries(results)) {
    if (Object.keys(counts).length > 0) next[key] = counts;
  }
  return next;
}

/** Keys and rules sorted, one rendering per line group: a diff of this file reads as a changelog. */
export function serialise(baseline: Baseline): string {
  const sorted: Baseline = {};
  for (const key of Object.keys(baseline).sort()) {
    const counts = baseline[key] ?? {};
    const rules: RuleCounts = {};
    for (const rule of Object.keys(counts).sort()) rules[rule] = counts[rule] ?? 0;
    sorted[key] = rules;
  }
  return `${JSON.stringify(sorted, null, 2)}\n`;
}

/** Per rule: total nodes, and on how many renderings — the summary `VISUAL-TESTING.md` quotes. */
export function totalsByRule(baseline: Baseline): Record<string, { nodes: number; renderings: number }> {
  const totals: Record<string, { nodes: number; renderings: number }> = {};
  for (const counts of Object.values(baseline)) {
    for (const [rule, nodes] of Object.entries(counts)) {
      const total = totals[rule] ?? { nodes: 0, renderings: 0 };
      total.nodes += nodes;
      total.renderings += 1;
      totals[rule] = total;
    }
  }
  return totals;
}
