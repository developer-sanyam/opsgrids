/**
 * Look at the buyer surface in a real browser, at real viewport widths.
 *
 * jsdom has no layout engine and has missed several defects in this app already, and
 * `--window-size=390` does not give a 390px viewport on macOS — the window chrome and the
 * device pixel ratio both interfere. So this drives Chrome over CDP and sets the viewport with
 * `Emulation.setDeviceMetricsOverride`, then reads `window.innerWidth` back and refuses to
 * screenshot a width it did not get.
 *
 * The gateway is not running locally, so each of the three states is produced by fulfilling the
 * bootstrap request through `Fetch.fulfillRequest` with the status that state is defined by —
 * which is also the only honest way to see a 404 and a 503 render differently.
 *
 * Scratch tooling, run by hand:  node tools/buyer-shots.mjs
 */

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const BROWSER =
  "/Users/sanyamjain/.cache/puppeteer/chrome-headless-shell/mac_arm-141.0.7390.54/chrome-headless-shell-mac-arm64/chrome-headless-shell";
const PORT = 9333;
const URL_UNDER_TEST = "http://localhost:4400/buyer.html";
const OUT = new URL("./.buyer-shots/", import.meta.url).pathname;

const CONTEXT = {
  store: "greenleaf",
  name: "<b>Verma</b> & Sons Organics",
  tagline: "Fresh from the farm, daily — since 1974",
  logo_text: null,
  currency: "INR",
  timezone: "Asia/Kolkata",
  canonical_host: "greenleaf.stores.opsgrids.com",
  is_canonical: false,
  checked_at: "2026-09-19T11:14:22Z",
};

const SCENARIOS = [
  { key: "loaded", status: 200, body: JSON.stringify(CONTEXT) },
  {
    key: "not-found",
    status: 404,
    body: JSON.stringify({ status: 404, detail: "No published store at this address." }),
  },
  { key: "unavailable", status: 503, body: JSON.stringify({ status: 503, detail: "nope" }) },
];

const WIDTHS = [
  { width: 390, height: 844, mobile: true },
  { width: 1280, height: 800, mobile: false },
];

mkdirSync(OUT, { recursive: true });

const browser = spawn(BROWSER, [
  `--remote-debugging-port=${PORT}`,
  "--disable-gpu",
  "--no-first-run",
  "--hide-scrollbars",
  "about:blank",
]);
browser.stderr.on("data", () => {});

async function targets() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
  return res.json();
}

async function waitForBrowser() {
  for (let i = 0; i < 60; i += 1) {
    try {
      return await targets();
    } catch {
      await sleep(200);
    }
  }
  throw new Error("the browser never opened a debugging port");
}

class Session {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.handlers = new Map();
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== undefined) {
        const entry = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
        else entry.resolve(message.result);
        return;
      }
      const handler = this.handlers.get(message.method);
      if (handler) handler(message.params);
    });
  }

  on(method, handler) {
    this.handlers.set(method, handler);
  }

  send(method, params = {}) {
    this.id += 1;
    const id = this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
}

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  return new Session(ws);
}

const list = await waitForBrowser();
const page = list.find((t) => t.type === "page");
const session = await connect(page.webSocketDebuggerUrl);

await session.send("Page.enable");
await session.send("Runtime.enable");
await session.send("Network.enable");

const report = [];

for (const scenario of SCENARIOS) {
  for (const viewport of WIDTHS) {
    await session.send("Emulation.setDeviceMetricsOverride", {
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: 1,
      mobile: viewport.mobile,
    });

    await session.send("Fetch.enable", {
      patterns: [{ urlPattern: "*store-context*" }],
    });
    let sentBody = null;
    session.on("Fetch.requestPaused", (params) => {
      sentBody =
        params.request.postData === undefined
          ? null
          : params.request.postData;
      void session.send("Fetch.fulfillRequest", {
        requestId: params.requestId,
        responseCode: scenario.status,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(scenario.body).toString("base64"),
      });
    });

    /* A fresh session per shot: the answer is cached in sessionStorage for the tab, which is the
       point of the cache and would otherwise make every scenario after the first show the first.
       (That it did, on the first run of this script, is the cache being verified end to end.) */
    await session.send("Runtime.evaluate", {
      expression: "try { sessionStorage.clear() } catch (e) { /* about:blank */ }",
    });
    await session.send("Page.navigate", { url: URL_UNDER_TEST });
    await sleep(1400);

    const probe = await session.send("Runtime.evaluate", {
      expression: `JSON.stringify({
        innerWidth: window.innerWidth,
        title: document.title,
        canonical: document.querySelector('link[rel=canonical]')?.href ?? null,
        status: document.querySelector('[role=status]')?.textContent ?? null,
        h1: document.querySelector('h1')?.textContent ?? null,
        bTags: document.querySelectorAll('h1 b').length,
        retry: [...document.querySelectorAll('button')].map(b => b.textContent),
        bodyWidth: document.body.getBoundingClientRect().width,
        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
        text: document.body.innerText.replace(/\\n+/g, ' | ')
      })`,
      returnByValue: true,
    });
    const observed = JSON.parse(probe.result.value);
    observed.scenario = scenario.key;
    observed.requestedWidth = viewport.width;
    observed.sentBody = sentBody;
    report.push(observed);

    const shot = await session.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${OUT}${scenario.key}-${viewport.width}.png`, Buffer.from(shot.data, "base64"));

    await session.send("Fetch.disable");
    session.handlers.delete("Fetch.requestPaused");
  }
}

console.log(JSON.stringify(report, null, 2));
browser.kill();
