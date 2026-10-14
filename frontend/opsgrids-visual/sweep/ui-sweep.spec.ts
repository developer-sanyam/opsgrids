/**
 * The layout sweep: loads every route of every app in sample mode at four widths and flags the
 * class of defect a human spots in seconds and a unit test never does — controls in a row that do
 * not line up, content that escapes its card, labels that wrap and push inputs down, chart ticks
 * that collide, a page with no room under its last line.
 *
 *   pnpm --filter @opsgrids/visual sweep
 *   SWEEP_APPS=product-engage,product-finance SWEEP_WIDTHS=1280 pnpm --filter @opsgrids/visual sweep
 *
 * `frontend/VISUAL-TESTING.md` ("The layout sweep") lists every knob and what each category means.
 */

import { spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { join } from "node:path";

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { mockApi } from "../lib/api";
import { DASHBOARD_ROUTES } from "../fixtures/api/routes";
import { unsignedToken } from "../lib/session";
import { detect, measureBottomGap } from "./detector.mjs";
import { APPS, FRONTEND_ROOT, selectedApps, sweepOut, sweepWidths } from "./sweep-config";

interface Hit {
  cat: string;
  kind: string;
  sig: string;
  text: string;
  rects: { x: number; y: number; w: number; h: number }[];
  detail: string;
}
interface RecordedHit extends Hit {
  width: number;
  shot?: string | undefined;
}
interface RouteResult {
  path: string;
  skipped?: string;
  hits: RecordedHit[];
}

const THEME = process.env["SWEEP_THEME"] === "dark" ? "dark" : "light";
const ROUTE_FILTER = process.env["SWEEP_ROUTE"];
const MAX_PAGES = Number(process.env["SWEEP_MAX_PAGES"] ?? 140);
const PARAM_SAMPLES = 2;
const BOTTOM_MIN = 16;
const HEIGHT: Record<number, number> = { 390: 844, 768: 1024, 1280: 800, 1600: 900 };

function portFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const s = createServer();
    s.once("error", () => resolve(false));
    s.once("listening", () => s.close(() => resolve(true)));
    s.listen(port, "127.0.0.1");
  });
}

async function freePort(start: number): Promise<number> {
  for (let p = start; p < 5600; p += 1) {
    // 5360-5420 and 4310-4500 belong to other sessions.
    if (await portFree(p)) return p;
  }
  throw new Error("no free port in 5500-5599");
}

async function waitUp(url: string, child: ChildProcess): Promise<void> {
  for (let i = 0; i < 120; i += 1) {
    if (child.exitCode !== null) throw new Error(`dev server exited with ${child.exitCode}`);
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`dev server at ${url} did not come up`);
}

/** Route patterns declared in an app's router file (and the files it spreads routes from). */
function staticPatterns(dir: string): string[] {
  const src = join(FRONTEND_ROOT, dir, "src");
  const files = [join(src, "main.tsx")];
  const found = new Set<string>();
  const re = /(?:path:\s*|lazyRoute\(\s*|guardedRoute\(\s*|stockRoute\(\s*|path=)["']([^"']+)["']/g;
  for (const f of files) {
    let text = "";
    try {
      text = readFileSync(f, "utf8");
    } catch {
      continue;
    }
    for (const m of text.matchAll(re)) {
      const raw = m[1] ?? "";
      if (raw === "*" || raw.startsWith("/login") || raw.includes("forgot") || raw.includes("reset-password")) continue;
      const p = (raw.startsWith("/") ? raw : `/${raw}`).replace(/\/\*$/, "");
      found.add(p === "" ? "/" : p);
    }
  }
  found.add("/");
  return [...found];
}

const slug = (p: string) => (p === "/" ? "root" : p.replace(/^\//, "").replace(/[^a-z0-9]+/gi, "-").replace(/-$/, ""));
const patternToRe = (p: string) => new RegExp(`^${p.replace(/:[^/]+/g, "[^/]+")}$`);

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => undefined);
  await page
    .waitForFunction(() => document.querySelectorAll(".animate-pulse, [aria-busy='true']").length === 0, undefined, { timeout: 8000 })
    .catch(() => undefined);
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  await page.waitForTimeout(250);
}

async function highlightAndShoot(page: Page, hit: Hit, file: string): Promise<boolean> {
  const rects = hit.rects.filter((r) => r.w > 0 && r.h > 0);
  if (rects.length === 0) return false;
  const first = rects[0];
  if (first === undefined) return false;
  const docH = await page.evaluate(() => document.documentElement.scrollHeight);
  const docW = await page.evaluate(() => document.documentElement.clientWidth);
  await page.evaluate((rs) => {
    rs.forEach((r, i) => {
      const d = document.createElement("div");
      d.setAttribute("data-sweep-overlay", "");
      d.style.cssText = `position:absolute;left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;outline:2px solid ${i === 0 ? "#ff1744" : "#2979ff"};outline-offset:0;pointer-events:none;z-index:2147483647;`;
      document.body.appendChild(d);
    });
  }, rects);
  const pad = 56;
  const minX = Math.min(...rects.map((r) => r.x));
  const minY = Math.min(...rects.map((r) => r.y));
  const maxX = Math.max(...rects.map((r) => r.x + r.w));
  const maxY = Math.max(...rects.map((r) => r.y + r.h));
  const x = Math.max(0, minX - pad);
  const y = Math.max(0, minY - pad);
  const w = Math.min(docW - x, maxX - minX + pad * 2);
  const h = Math.min(docH - y, Math.min(maxY - minY, 900) + pad * 2);
  await page.screenshot({ path: file, fullPage: true, clip: { x, y, width: Math.max(w, 20), height: Math.max(h, 20) } }).catch(() => undefined);
  await page.evaluate(() => document.querySelectorAll("[data-sweep-overlay]").forEach((n) => n.remove()));
  return true;
}

async function sweepApp(appName: string): Promise<void> {
  const app = APPS[appName];
  if (app === undefined) throw new Error(`unknown app ${appName}`);
  const out = sweepOut();
  const shots = join(out, "shots", appName);
  mkdirSync(shots, { recursive: true });
  const widths = sweepWidths();
  const index = Object.keys(APPS).indexOf(appName);
  const port = await freePort(5500 + index * 3);
  const origin = `http://127.0.0.1:${port}`;

  const server = spawn("pnpm", ["exec", "vite", "--port", String(port), "--strictPort", "--host", "127.0.0.1"], {
    cwd: join(FRONTEND_ROOT, app.dir),
    env: { ...process.env, VITE_USE_FIXTURES: "true" },
    stdio: "ignore",
  });
  const results: RouteResult[] = [];
  try {
    await waitUp(origin, server);
    const browser = await (await import("@playwright/test")).chromium.launch();
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "en-IN", timezoneId: "Asia/Kolkata" });
    await context.addInitScript(
      ({ slot, theme, token }) => {
        localStorage.setItem(`opsgrids.${slot}.theme`, theme);
        if (token) sessionStorage.setItem(`opsgrids.${slot}.access_token`, token);
        // Reduced motion in effect: nothing is mid-animation when measured.
      },
      {
        slot: app.slot,
        theme: THEME,
        token:
          app.mode === "harness"
            ? unsignedToken({
                sub: "5b1f4c2a-7d3e-4a8b-9c6d-1e2f3a4b5c6d",
                act_org: "8a7b6c5d-4e3f-4a1b-8c9d-0e1f2a3b4c5d",
                ent: ["crm", "hrms", "finance", "projects", "operations"],
                pv: 7,
                aud: app.slot,
                roles: ["owner"],
              })
            : "",
      },
    );
    const page = await context.newPage();
    await page.emulateMedia({ reducedMotion: "reduce" });
    if (app.mode === "harness") {
      await context.route(`${origin}/api/**`, (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(route.request().method() === "GET" ? { items: [], total: 0, page: 1, size: 25, pages: 0, next_cursor: null } : {}),
        }),
      );
      await mockApi(page, origin, DASHBOARD_ROUTES);
    }

    const patterns = staticPatterns(app.dir);
    const concrete = patterns.filter((p) => !p.includes(":"));
    const paramPatterns = patterns.filter((p) => p.includes(":"));
    const queue: string[] = [...concrete];
    const queued = new Set(queue);
    const sampled = new Map<string, number>();
    const login = (url: string) => /\/(login|signup|forgot|reset|accept)/.test(url);

    while (queue.length > 0 && results.filter((r) => r.skipped === undefined).length < MAX_PAGES) {
      const path = queue.shift() as string;
      if (ROUTE_FILTER !== undefined && !path.includes(ROUTE_FILTER)) continue;
      const result: RouteResult = { path, hits: [] };
      try {
        await page.setViewportSize({ width: 1280, height: 800 });
        await page.goto(`${origin}${path}`, { waitUntil: "domcontentloaded", timeout: 30000 });
        await page.locator("main, #root > *").first().waitFor({ timeout: 15000 });
        await settle(page);
      } catch (error) {
        result.skipped = `did not render: ${String(error).slice(0, 120)}`;
        results.push(result);
        continue;
      }
      const landed = new URL(page.url());
      if (login(landed.pathname) && !login(path)) {
        result.skipped = "redirected to sign-in (needs a real session)";
        results.push(result);
        continue;
      }
      const heading = (await page.locator("h1").first().textContent({ timeout: 1500 }).catch(() => "")) ?? "";
      const body = (await page.locator("main").first().innerText({ timeout: 1500 }).catch(() => "")) ?? "";
      if (/page not found|404/i.test(heading) || (heading === "" && /page not found/i.test(body))) {
        result.skipped = "not a page (404)";
        results.push(result);
        continue;
      }

      // Discover links for param routes and for screens the router file does not list.
      const hrefs = await page.evaluate(() =>
        Array.from(document.querySelectorAll("a[href]"))
          .map((a) => (a as HTMLAnchorElement).href)
          .filter((h) => h.startsWith(location.origin)),
      );
      for (const h of hrefs) {
        const p = new URL(h).pathname.replace(/\/$/, "") || "/";
        if (queued.has(p) || login(p)) continue;
        const pat = paramPatterns.find((pp) => patternToRe(pp).test(p));
        if (pat !== undefined) {
          const n = sampled.get(pat) ?? 0;
          if (n >= PARAM_SAMPLES) continue;
          sampled.set(pat, n + 1);
        } else if (!concrete.includes(p) && queue.length + results.length > MAX_PAGES) continue;
        queued.add(p);
        queue.push(p);
      }

      const seen = new Set<string>();
      for (const width of widths) {
        await page.setViewportSize({ width, height: HEIGHT[width] ?? 900 });
        await page.waitForTimeout(350);
        await page.evaluate(() => window.scrollTo(0, 0));
        const found: Hit[] = await page.evaluate(detect, { alignTolerance: 2 });
        // (f) scrolled to the end.
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        await page.waitForTimeout(120);
        const bottom = await page.evaluate(measureBottomGap);
        await page.evaluate(() => window.scrollTo(0, 0));
        if (bottom.scrollable && bottom.atEnd && bottom.gap < BOTTOM_MIN) {
          found.push({ cat: "f", kind: "no-bottom-space", sig: bottom.sig, text: bottom.text, rects: [], detail: `${bottom.gap}px below the last content (min ${BOTTOM_MIN})` });
        }
        let shotCount = 0;
        for (const hit of found) {
          const key = `${width}|${hit.cat}|${hit.kind}|${hit.sig}|${hit.text.slice(0, 20)}`;
          if (seen.has(key)) continue;
          seen.add(key);
          const rec: RecordedHit = { ...hit, width };
          if (shotCount < 6 && hit.rects.length > 0) {
            const file = `${slug(path)}-${width}-${hit.cat}-${shotCount}.png`;
            if (await highlightAndShoot(page, hit, join(shots, file))) {
              rec.shot = `shots/${appName}/${file}`;
              shotCount += 1;
            }
          }
          result.hits.push(rec);
        }
      }
      results.push(result);
    }
    await browser.close();
  } finally {
    server.kill("SIGTERM");
  }

  const byCat: Record<string, number> = {};
  let hits = 0;
  for (const r of results) {
    for (const h of r.hits) {
      byCat[h.cat] = (byCat[h.cat] ?? 0) + 1;
      // "i" is informational (deliberate truncation without a title): counted, not a hit.
      if (h.cat !== "i") hits += 1;
    }
  }
  writeFileSync(join(out, `${appName}.json`), JSON.stringify({ app: appName, theme: THEME, widths, hits, byCat, routes: results }, null, 2));
  expect(results.length).toBeGreaterThan(0);
}

for (const name of selectedApps()) {
  test(`layout sweep: ${name}`, async () => {
    await sweepApp(name);
  });
}
