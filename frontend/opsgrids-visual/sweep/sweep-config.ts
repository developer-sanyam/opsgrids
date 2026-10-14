import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export const FRONTEND_ROOT = fileURLToPath(new URL("../..", import.meta.url));

export interface SweepApp {
  /** Directory under `frontend/`. */
  dir: string;
  /** The `opsgrids.<slot>.*` session and theme keys the app uses. */
  slot: string;
  /**
   * `fixtures`: the app has its own `VITE_USE_FIXTURES` sample mode. `harness`: it has none, so
   * the sweep answers its API from the visual harness's contract-shaped mocks (anything those do
   * not cover answers as an empty list).
   */
  mode: "fixtures" | "harness";
}

export const APPS: Record<string, SweepApp> = {
  "opsgrids-dashboard": { dir: "opsgrids-dashboard", slot: "dashboard", mode: "harness" },
  "opsgrids-internal": { dir: "opsgrids-internal", slot: "internal", mode: "fixtures" },
  "product-crm": { dir: "product-crm", slot: "crm", mode: "fixtures" },
  "product-engage": { dir: "product-engage", slot: "engage", mode: "fixtures" },
  "product-finance": { dir: "product-finance", slot: "finance", mode: "fixtures" },
  "product-hrms": { dir: "product-hrms", slot: "hrms", mode: "fixtures" },
  "product-itsm": { dir: "product-itsm", slot: "itsm", mode: "fixtures" },
  "product-marketing": { dir: "product-marketing", slot: "marketing", mode: "fixtures" },
  "product-operations": { dir: "product-operations", slot: "operations", mode: "fixtures" },
  "product-projects": { dir: "product-projects", slot: "projects", mode: "fixtures" },
  "product-pulseboard": { dir: "product-pulseboard", slot: "pulseboard", mode: "fixtures" },
  "product-storefront": { dir: "product-storefront", slot: "storefront", mode: "fixtures" },
  "product-support": { dir: "product-support", slot: "support", mode: "fixtures" },
  "worker-app": { dir: "worker-app", slot: "worker", mode: "harness" },
};

export function selectedApps(): string[] {
  const raw = process.env["SWEEP_APPS"];
  return raw === undefined || raw === "" ? Object.keys(APPS) : raw.split(",").map((s) => s.trim());
}

export function sweepWidths(): number[] {
  return (process.env["SWEEP_WIDTHS"] ?? "320,360,390,414").split(",").map(Number);
}

export function sweepOut(): string {
  const out = process.env["SWEEP_OUT"] ?? join(FRONTEND_ROOT, "opsgrids-visual", "test-results", "sweep");
  mkdirSync(out, { recursive: true });
  return out;
}

/** The ports the sweep's dev servers may use: `[start, end)`. Defaults to the sweep's own 5600-5699;
 *  a session that was given a different range sets `SWEEP_PORT_START` and `SWEEP_PORT_END`. */
export function sweepPorts(): { start: number; end: number } {
  return {
    start: Number(process.env["SWEEP_PORT_START"] ?? 5600),
    end: Number(process.env["SWEEP_PORT_END"] ?? 5700),
  };
}
