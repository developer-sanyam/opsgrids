import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { sweepOut } from "./sweep-config";

interface AppReport {
  app: string;
  hits: number;
  byCat: Record<string, number>;
  routes: { path: string; skipped?: string; hits: unknown[] }[];
}

/** Folds the per-app reports into `report.json` and prints the table a PR description quotes. */
export default function mergeReport(): void {
  const out = sweepOut();
  const files = readdirSync(out).filter((f) => f.endsWith(".json") && f !== "report.json");
  const apps = files.map((f) => JSON.parse(readFileSync(join(out, f), "utf8")) as AppReport);
  apps.sort((a, b) => a.app.localeCompare(b.app));
  const total = apps.reduce((n, a) => n + a.hits, 0);
  writeFileSync(join(out, "report.json"), JSON.stringify({ total, apps }, null, 2));
  const cats = ["a", "b", "c", "d", "e", "f", "i"];
  const lines = [`${"app".padEnd(22)}${"pages".padStart(6)}${cats.map((c) => c.padStart(6)).join("")}${"total".padStart(7)}`];
  for (const a of apps) {
    const pages = a.routes.filter((r) => r.skipped === undefined).length;
    lines.push(
      `${a.app.padEnd(22)}${String(pages).padStart(6)}${cats.map((c) => String(a.byCat[c] ?? 0).padStart(6)).join("")}${String(a.hits).padStart(7)}`,
    );
  }
  lines.push(`${"TOTAL".padEnd(28)}${" ".repeat(42)}${String(total).padStart(7)}`);
  console.log(`\n${lines.join("\n")}\n\nreport: ${join(out, "report.json")}`);
}
