/**
 * Two things jsdom cannot answer, asked of a real layout engine.
 *
 * 1. **Does the header move when the shop arrives?** The skeleton exists to reserve the band the
 *    brand bar will occupy; if the two heights differ, the page jumps under a buyer's thumb.
 * 2. **Is the retry button keyboard-reachable with a visible focus ring?** Removing a focus ring
 *    makes a page unusable by keyboard, and this is the only control on the buyer surface.
 *
 * Scratch tooling, run by hand:  node tools/buyer-shift-focus.mjs
 */

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const BROWSER =
  "/Users/sanyamjain/.cache/puppeteer/chrome-headless-shell/mac_arm-141.0.7390.54/chrome-headless-shell-mac-arm64/chrome-headless-shell";
const PORT = 9334;
const URL_UNDER_TEST = "http://localhost:4400/buyer.html";

const CONTEXT = {
  store: "greenleaf",
  name: "Greenleaf Organics",
  tagline: "Fresh from the farm, daily",
  logo_text: "GLO",
  currency: "INR",
  timezone: "Asia/Kolkata",
  canonical_host: "greenleaf.stores.opsgrids.com",
  is_canonical: true,
  checked_at: "2026-09-19T11:14:22Z",
};

const browser = spawn(BROWSER, [
  `--remote-debugging-port=${PORT}`,
  "--disable-gpu",
  "--no-first-run",
  "--hide-scrollbars",
  "about:blank",
]);
browser.stderr.on("data", () => {});

async function targets() {
  for (let i = 0; i < 60; i += 1) {
    try {
      return await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    } catch {
      await sleep(200);
    }
  }
  throw new Error("the browser never opened a debugging port");
}

const page = (await targets()).find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));

let nextId = 0;
const pending = new Map();
const handlers = new Map();
ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.id !== undefined) {
    const entry = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
    else entry.resolve(message.result);
    return;
  }
  const handler = handlers.get(message.method);
  if (handler) handler(message.params);
});

function send(method, params = {}) {
  nextId += 1;
  const id = nextId;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

const evaluate = (expression) =>
  send("Runtime.evaluate", { expression, returnByValue: true }).then((r) => r.result.value);

function fulfil(requestId, status, body) {
  return send("Fetch.fulfillRequest", {
    requestId,
    responseCode: status,
    responseHeaders: [{ name: "Content-Type", value: "application/json" }],
    body: Buffer.from(body).toString("base64"),
  });
}

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", {
  width: 390,
  height: 844,
  deviceScaleFactor: 1,
  mobile: true,
});

/* ---------------- 1. the header band, before and after ---------------- */

await send("Fetch.enable", { patterns: [{ urlPattern: "*store-context*" }] });
handlers.set("Fetch.requestPaused", async (params) => {
  // Hold the answer so the skeleton is on screen long enough to measure.
  await sleep(900);
  await fulfil(params.requestId, 200, JSON.stringify(CONTEXT));
});

await send("Page.navigate", { url: URL_UNDER_TEST });
await sleep(450);
const skeletonBand = await evaluate(
  `(() => { const n = document.querySelector('[aria-hidden] > div'); return n ? Math.round(n.getBoundingClientRect().height) : null })()`,
);
await sleep(1400);
const loadedBand = await evaluate(
  `Math.round(document.querySelector('header').getBoundingClientRect().height)`,
);
console.log(`skeleton header band: ${skeletonBand}px    loaded header: ${loadedBand}px`);

/* ---------------- 2. keyboard reach and the focus ring ---------------- */

await send("Fetch.disable");
handlers.delete("Fetch.requestPaused");
await send("Fetch.enable", { patterns: [{ urlPattern: "*store-context*" }] });
handlers.set("Fetch.requestPaused", (params) => fulfil(params.requestId, 503, "{}"));

await evaluate("sessionStorage.clear()");
await send("Page.navigate", { url: URL_UNDER_TEST });
await sleep(1200);

for (const type of ["rawKeyDown", "keyUp"]) {
  await send("Input.dispatchKeyEvent", {
    type,
    key: "Tab",
    code: "Tab",
    windowsVirtualKeyCode: 9,
    nativeVirtualKeyCode: 9,
  });
}
await sleep(250);

console.log(
  "focus after one Tab:",
  await evaluate(
    `(() => { const a = document.activeElement; const s = getComputedStyle(a);
      return JSON.stringify({ tag: a.tagName, text: a.textContent,
        outline: s.outlineStyle + ' ' + s.outlineWidth + ' ' + s.outlineColor,
        boxShadow: s.boxShadow }) })()`,
  ),
);
console.log(
  "document structure:",
  await evaluate(
    `JSON.stringify({ h1: document.querySelectorAll('h1').length,
      liveRegions: document.querySelectorAll('[aria-live]').length })`,
  ),
);

browser.kill();
