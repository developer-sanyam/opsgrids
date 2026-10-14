import { readFileSync, readdirSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Sample-data mode (`VITE_USE_FIXTURES=true`) must serve every screen without a gateway. The bug
 * this pins: the Communication overview, WhatsApp and Email dashboards, the WhatsApp template
 * library, automations and the email templates all called the gateway directly, so in sample mode
 * each opened as "Request failed with 500" while the screens that had fixtures worked. A read added
 * later with no fixture branch fails the structural test below; a branch that exists but returns
 * the wrong thing fails the runtime one.
 */

// Vitest runs from the package root.
const API_DIR = `${process.cwd()}/src/api/`;

/** Every exported function in an API module, with its source. */
function functionsOf(file: string): { name: string; source: string }[] {
  const text = readFileSync(`${API_DIR}${file}`, "utf8");
  return text
    .split(/\n(?=export (?:async )?function )/)
    .flatMap((chunk) => {
      const match = /^export (?:async )?function (\w+)/.exec(chunk);
      return match?.[1] === undefined ? [] : [{ name: match[1], source: chunk }];
    });
}

const API_FILES = readdirSync(API_DIR).filter(
  (f) => f.endsWith(".ts") && !f.endsWith(".test.ts") && !f.endsWith(".fixtures.ts") && f !== "types.ts",
);

describe("every request function answers in sample mode", () => {
  for (const file of API_FILES) {
    for (const fn of functionsOf(file)) {
      if (!/apiRequest|apiFetch/.test(fn.source)) continue;
      it(`${file}: ${fn.name} has a USE_FIXTURES branch`, () => {
        expect(fn.source).toContain("USE_FIXTURES");
      });
    }
  }
});

describe("sample-mode reads", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("VITE_USE_FIXTURES", "true");
    // Any request that escapes to the network is the bug.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("a sample-mode read reached the network"))),
    );
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  const RANGE = { from: "2026-10-01", to: "2026-10-03" };

  it("serves the Communication overview in the shape the service sends", async () => {
    const analytics = await import("./analytics");
    const hub = await analytics.getHubOverview(RANGE);
    expect(hub.channels.map((c) => c.key)).toEqual(["whatsapp", "email", "sms", "push"]);
    expect(hub.whatsapp?.sent).toBeGreaterThan(0);
    expect(hub.email?.sent).toBeGreaterThan(0);
    expect(hub.costs?.by_channel.map((c) => c.channel)).toEqual(["whatsapp", "email"]);
    expect(hub.costs?.estimated).toBe(true);
  });

  it("follows the requested range in the daily series", async () => {
    const analytics = await import("./analytics");
    const wa = await analytics.getWhatsAppOverview(RANGE);
    expect(wa.series.map((p) => p.date)).toEqual(["2026-10-01", "2026-10-02", "2026-10-03"]);
    const email = await analytics.getEmailOverview(RANGE);
    expect(email.series).toHaveLength(3);
    const campaign = await analytics.getCampaignAnalytics("any");
    expect(campaign.recipients).toBeGreaterThan(0);
  });

  it("serves the WhatsApp, template, automation and email reads without the network", async () => {
    const signup = await import("./whatsappSignup");
    expect((await signup.getSignupConfig()).enabled).toBe(false);
    expect((await signup.listWhatsAppNumbers()).items.length).toBeGreaterThan(0);
    expect((await signup.getWhatsAppHealth()).connected).toBe(true);

    const templates = await import("./whatsappTemplates");
    expect((await templates.listTemplates()).items.length).toBeGreaterThan(0);
    expect((await templates.listTemplates({ status: "approved" })).items.every((t) => t.status === "approved")).toBe(true);
    expect((await templates.getTemplate("delivery_confirmed")).history.length).toBeGreaterThan(0);
    expect((await templates.previewTemplate("delivery_confirmed", { "1": "Asha" })).text).toContain("Asha");

    const automations = await import("./automations");
    expect((await automations.listAutomations()).items.length).toBeGreaterThan(0);
    expect((await automations.listTriggers()).length).toBeGreaterThan(0);
    const [first] = (await automations.listAutomations()).items;
    expect((await automations.getAutomation(first?.id ?? "")).graph.nodes.length).toBeGreaterThan(0);
    expect((await automations.listRuns(first?.id ?? "")).items.length).toBeGreaterThan(0);
    expect((await automations.getStats()).executions).toBeGreaterThan(0);
    expect((await automations.listWhatsAppTemplateOptions()).length).toBeGreaterThan(0);
    expect((await automations.listEmailTemplateOptions()).length).toBeGreaterThan(0);

    const email = await import("./email");
    expect((await email.listEmailTemplates()).items.length).toBeGreaterThan(0);
    expect((await email.countCampaignsByStatus()).all).toBeGreaterThan(0);
  });

  it("refuses a write and says it is sample data, without a request", async () => {
    const templates = await import("./whatsappTemplates");
    await expect(templates.syncTemplates()).rejects.toThrow(/sample data/);
    // An ApiError, so the screens show the sentence instead of "Couldn't reach the server".
    await expect(templates.syncTemplates()).rejects.toBeInstanceOf((await import("@opsgrids/client")).ApiError);
    const email = await import("./email");
    await expect(
      email.saveEmailTemplate({ code: "x", channel: "email", body: "b" }),
    ).rejects.toThrow(/sample data/);
    const automations = await import("./automations");
    await expect(automations.pauseAutomation("x")).rejects.toThrow(/sample data/);
  });
});
