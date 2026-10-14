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
import { detect, detectMobile, detectOverlay, focusObscured, installHelpers, measureBottomGap } from "./detector.mjs";
import { APPS, FRONTEND_ROOT, selectedApps, sweepOut, sweepWidths } from "./sweep-config";

interface Owner {
  shared: boolean;
  component: string;
  file: string;
}
interface Hit {
  cat: string;
  kind: string;
  sig: string;
  text: string;
  rects: { x: number; y: number; w: number; h: number }[];
  detail: string;
  /** The component that rendered the offending element (from React's dev creation stack). */
  owner?: Owner | null;
}
interface RecordedHit extends Omit<Hit, "owner"> {
  width: number;
  /** An `opsgrids-ui` / `opsgrids-client` component name, or `app-local`. */
  component: string;
  shared: boolean;
  /** Found with a dialog, drawer or menu open: the control that was opened. */
  probe?: string | undefined;
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
const HEIGHT: Record<number, number> = { 320: 640, 360: 780, 390: 844, 414: 896, 768: 1024, 1280: 800, 1600: 900 };
/** Below this width the mobile checks (g, h, j, k, l, m) run. */
const MOBILE_BELOW = 768;
/** Categories deduplicated by component and shape rather than by the offending text. */
const SHAPE_CATS = new Set(["g", "h", "j", "k", "l", "m"]);
const PROBES_PER_ROUTE = 5;

function portFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const s = createServer();
    s.once("error", () => resolve(false));
    s.once("listening", () => s.close(() => resolve(true)));
    s.listen(port, "127.0.0.1");
  });
}

async function freePort(start: number): Promise<number> {
  for (let p = start; p < 5700; p += 1) {
    // The sweep owns 5600-5699; every other range belongs to other sessions.
    if (await portFree(p)) return p;
  }
  throw new Error("no free port in 5600-5699");
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

/** A screenshot of the viewport as it is (an open dialog is `position: fixed`), offending box outlined. */
async function highlightViewport(page: Page, hit: Hit, file: string): Promise<boolean> {
  const rects = hit.rects.filter((r) => r.w > 0 && r.h > 0);
  if (rects.length === 0) return false;
  await page.evaluate((rs) => {
    rs.forEach((r, i) => {
      const d = document.createElement("div");
      d.setAttribute("data-sweep-overlay", "");
      d.style.cssText = `position:fixed;left:${r.x - scrollX}px;top:${r.y - scrollY}px;width:${r.w}px;height:${r.h}px;outline:2px solid ${i === 0 ? "#ff1744" : "#2979ff"};pointer-events:none;z-index:2147483647;`;
      document.body.appendChild(d);
    });
  }, rects);
  await page.screenshot({ path: file }).catch(() => undefined);
  await page.evaluate(() => document.querySelectorAll("[data-sweep-overlay]").forEach((n) => n.remove()));
  return true;
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

/** Buttons worth opening to look at what they open: dialogs, drawers, menus, sheets. */
async function probeCandidates(page: Page, seenLabels: Set<string>): Promise<string[]> {
  const labels = await page.evaluate(() => {
    const WANT = /^(add|new|create|invite|import|export|filters?|more|edit|assign|schedule|upload|open|menu|apply|log|record|send|compose|share|columns|bulk|actions?|notifications?|search|account|profile|sort|view|manage|configure|connect|generate|set|change|update)\b|menu|navigation|options|command/i;
    const SKIP = /sign out|log ?out|delete|remove|cancel|close|reset|dismiss|theme|appearance|collapse|expand sidebar/i;
    const out: string[] = [];
    const firstWords = new Set<string>();
    document.querySelectorAll("button, [role='button']").forEach((node) => {
      const b = node as HTMLButtonElement;
      const r = b.getBoundingClientRect();
      const s = getComputedStyle(b);
      if (r.width === 0 || r.height === 0 || s.visibility === "hidden" || b.disabled || b.type === "submit") return;
      if (b.closest("[role='dialog'], [aria-hidden='true'], [inert], tr, [role='row']")) return;
      const label = (b.getAttribute("aria-label") || b.textContent || "").replace(/\s+/g, " ").trim().slice(0, 48);
      if (label === "" || SKIP.test(label)) return;
      if (!(WANT.test(label) || b.hasAttribute("aria-haspopup") || b.getAttribute("aria-expanded") === "false")) return;
      const first = label.toLowerCase().split(" ")[0] ?? "";
      if (firstWords.has(first)) return;
      firstWords.add(first);
      b.setAttribute("data-sweep-probe", String(out.length));
      out.push(label);
    });
    return out;
  });
  const picked: string[] = [];
  labels.forEach((label, index) => {
    if (seenLabels.has(label) || picked.length >= PROBES_PER_ROUTE) return;
    seenLabels.add(label);
    picked.push(`${index}|${label}`);
  });
  return picked;
}

async function sweepApp(appName: string): Promise<void> {
  const app = APPS[appName];
  if (app === undefined) throw new Error(`unknown app ${appName}`);
  const out = sweepOut();
  const shots = join(out, "shots", appName);
  mkdirSync(shots, { recursive: true });
  const widths = sweepWidths();
  const index = Object.keys(APPS).indexOf(appName);
  const port = await freePort(5600 + index * 4);
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
    /** Per width: the probe labels already opened somewhere in this app, so a header menu is opened once. */
    const probedLabels = new Map<number, Set<string>>();
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
        await page.evaluate(installHelpers);
        await page.evaluate(() => window.scrollTo(0, 0));
        const mobile = width < MOBILE_BELOW;
        const found: Hit[] = await page.evaluate(detect, { alignTolerance: 2 });
        if (mobile) {
          found.push(...(await page.evaluate(detectMobile)));
          found.push(...(await page.evaluate(focusObscured)));
        }
        // (f) scrolled to the end.
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        await page.waitForTimeout(120);
        const bottom = await page.evaluate(measureBottomGap);
        await page.evaluate(() => window.scrollTo(0, 0));
        if (bottom.scrollable && bottom.atEnd && bottom.gap < 0 && bottom.bar !== null) {
          found.push({ cat: "l", kind: "fixed-bar-covers-last-content", sig: bottom.barSig, text: bottom.text, rects: [], detail: `${-bottom.gap}px of the last content sits under a fixed bar`, owner: bottom.bar });
        } else if (bottom.scrollable && bottom.atEnd && bottom.gap < BOTTOM_MIN) {
          found.push({ cat: "f", kind: "no-bottom-space", sig: bottom.sig, text: bottom.text, rects: [], detail: `${bottom.gap}px below the last content (min ${BOTTOM_MIN})`, owner: bottom.owner });
        }
        let probed: string | undefined;
        let shotCount = 0;
        const record = async (hit: Hit, viewportShot: boolean) => {
          const key = SHAPE_CATS.has(hit.cat) ? `${width}|${hit.cat}|${hit.kind}|${hit.sig}` : `${width}|${hit.cat}|${hit.kind}|${hit.sig}|${hit.text.slice(0, 20)}`;
          if (seen.has(key)) return;
          seen.add(key);
          const { owner, ...rest } = hit;
          const rec: RecordedHit = { ...rest, width, component: owner?.component ?? "app-local", shared: owner?.shared ?? false, ...(probed !== undefined ? { probe: probed } : {}) };
          if (shotCount < 8 && hit.rects.length > 0) {
            const file = `${slug(path)}-${width}-${hit.cat}-${shotCount}.png`;
            const ok = viewportShot ? await highlightViewport(page, hit, join(shots, file)) : await highlightAndShoot(page, hit, join(shots, file));
            if (ok) {
              rec.shot = `shots/${appName}/${file}`;
              shotCount += 1;
            }
          }
          result.hits.push(rec);
        };
        for (const hit of found) await record(hit, false);
        if (mobile) {
          let labelsSeen = probedLabels.get(width);
          if (labelsSeen === undefined) {
            labelsSeen = new Set<string>();
            probedLabels.set(width, labelsSeen);
          }
          for (const entry of await probeCandidates(page, labelsSeen)) {
            const [index = "", label = ""] = entry.split("|");
            probed = label;
            try {
              await page.locator(`[data-sweep-probe="${index}"]`).first().click({ timeout: 2500 });
              await page.waitForTimeout(350);
              await page.evaluate(installHelpers);
              const open: Hit[] = [...(await page.evaluate(detectOverlay)), ...(await page.evaluate(detectMobile))];
              for (const hit of open) await record(hit, true);
            } catch {
              /* not clickable at this width: the control itself is reported by g and l */
            }
            await page.keyboard.press("Escape").catch(() => undefined);
            await page.waitForTimeout(150);
            const stillOpen = await page.locator("[role='dialog']:visible").count();
            if (stillOpen > 0 || new URL(page.url()).pathname !== landed.pathname) {
              await page.goto(`${origin}${path}`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => undefined);
              await settle(page);
              await page.setViewportSize({ width, height: HEIGHT[width] ?? 900 });
              await page.waitForTimeout(250);
              await page.evaluate(installHelpers);
            }
          }
          probed = undefined;
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
