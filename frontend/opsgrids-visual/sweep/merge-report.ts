import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { sweepOut } from "./sweep-config";

interface HitRecord {
  cat: string;
  kind: string;
  sig: string;
  text: string;
  detail: string;
  width: number;
  component?: string;
  shared?: boolean;
  probe?: string;
  shot?: string;
}

interface AppReport {
  app: string;
  hits: number;
  byCat: Record<string, number>;
  routes: { path: string; skipped?: string; hits: HitRecord[] }[];
}

const CATS = ["a", "b", "c", "d", "e", "f", "g", "h", "j", "k", "l", "m", "i"];

interface Group {
  component: string;
  shared: boolean;
  count: number;
  byCat: Record<string, number>;
  /** kind -> count */
  kinds: Record<string, number>;
  apps: Record<string, number>;
  /** One example per kind: where to look first. */
  examples: Record<string, { app: string; route: string; width: number; detail: string; shot?: string | undefined }>;
}

/**
 * Folds the per-app reports into `report.json` and prints the tables a PR description quotes:
 * the app table, then the hits grouped by the component that rendered the offending element, so
 * what is shared (fix once in `opsgrids-ui`) is separated from what is app-local.
 */
export default function mergeReport(): void {
  const out = sweepOut();
  const files = readdirSync(out).filter((f) => f.endsWith(".json") && !["report.json", "components.json"].includes(f));
  const apps = files.map((f) => JSON.parse(readFileSync(join(out, f), "utf8")) as AppReport);
  apps.sort((a, b) => a.app.localeCompare(b.app));
  const total = apps.reduce((n, a) => n + a.hits, 0);

  const groups = new Map<string, Group>();
  for (const a of apps) {
    for (const r of a.routes) {
      for (const h of r.hits) {
        if (h.cat === "i") continue;
        const component = h.component ?? "app-local";
        const g =
          groups.get(component) ??
          ({ component, shared: h.shared ?? false, count: 0, byCat: {}, kinds: {}, apps: {}, examples: {} } satisfies Group);
        groups.set(component, g);
        g.count += 1;
        g.byCat[h.cat] = (g.byCat[h.cat] ?? 0) + 1;
        g.kinds[`${h.cat}:${h.kind}`] = (g.kinds[`${h.cat}:${h.kind}`] ?? 0) + 1;
        g.apps[a.app] = (g.apps[a.app] ?? 0) + 1;
        const key = `${h.cat}:${h.kind}`;
        if (g.examples[key] === undefined) g.examples[key] = { app: a.app, route: r.path, width: h.width, detail: h.detail, shot: h.shot };
      }
    }
  }
  const components = [...groups.values()].sort((x, y) => Number(y.shared) - Number(x.shared) || y.count - x.count);
  const sharedTotal = components.filter((c) => c.shared).reduce((n, c) => n + c.count, 0);

  writeFileSync(join(out, "report.json"), JSON.stringify({ total, shared: sharedTotal, appLocal: total - sharedTotal, apps }, null, 2));
  writeFileSync(join(out, "components.json"), JSON.stringify({ total, shared: sharedTotal, appLocal: total - sharedTotal, components }, null, 2));

  const lines = [`${"app".padEnd(22)}${"pages".padStart(6)}${CATS.map((c) => c.padStart(6)).join("")}${"total".padStart(7)}${"shared".padStart(8)}`];
  for (const a of apps) {
    const pages = a.routes.filter((r) => r.skipped === undefined).length;
    const shared = a.routes.reduce((n, r) => n + r.hits.filter((h) => h.cat !== "i" && h.shared === true).length, 0);
    lines.push(
      `${a.app.padEnd(22)}${String(pages).padStart(6)}${CATS.map((c) => String(a.byCat[c] ?? 0).padStart(6)).join("")}${String(a.hits).padStart(7)}${String(shared).padStart(8)}`,
    );
  }
  lines.push(`${"TOTAL".padEnd(28)}${" ".repeat(CATS.length * 6)}${String(total).padStart(7)}${String(sharedTotal).padStart(8)}`);

  const byComponent = [`${"component".padEnd(28)}${"hits".padStart(6)}  categories`];
  for (const c of components) {
    const cats = Object.entries(c.byCat)
      .sort(([x], [y]) => x.localeCompare(y))
      .map(([k, v]) => `${k}:${v}`)
      .join(" ");
    byComponent.push(`${`${c.shared ? "" : "· "}${c.component}`.padEnd(28)}${String(c.count).padStart(6)}  ${cats}`);
  }
  const summary = `${lines.join("\n")}\n\nby component (shared first; "· app-local" is everything rendered by an app):\n${byComponent.join("\n")}\n\nshared ${sharedTotal}, app-local ${total - sharedTotal}\nreport: ${join(out, "report.json")}\ncomponents: ${join(out, "components.json")}`;
  writeFileSync(join(out, "summary.txt"), summary);
  console.log(`\n${summary}\n`);
}
