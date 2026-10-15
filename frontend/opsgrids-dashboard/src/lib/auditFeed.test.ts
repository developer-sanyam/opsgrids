import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import type { AuditEntry, AuditPage, AuditQuery } from "@/api/types";

import { activeSources, describeAction, everySourceRefused, initialStates, nextPage } from "./auditFeed";
import type { AuditSource } from "./auditFeed";

function entry(service: string, minute: number, id = `${service}-${minute}`): AuditEntry {
  return {
    id,
    at: new Date(Date.UTC(2026, 9, 1, 9, minute)).toISOString(),
    service,
    product: "crm",
    actor_id: null,
    by_operator: false,
    action: "x.updated",
    entity: "x",
    entity_id: null,
    fields: [],
    sensitive: false,
    reason: null,
  };
}

/** A fake source: rows newest first, paged by an index cursor exactly as the server pages. */
function server(rows: Record<string, AuditEntry[]>) {
  return vi.fn(async (service: string, params: AuditQuery): Promise<AuditPage> => {
    const all = rows[service] ?? [];
    const start = params.cursor ? Number(params.cursor) : 0;
    const limit = params.limit ?? 50;
    const items = all.slice(start, start + limit);
    const next = start + limit < all.length ? String(start + limit) : null;
    return { service, product: "crm", items, next_cursor: next };
  });
}

const A: AuditSource = { service: "a", product: null, label: "A" };
const B: AuditSource = { service: "b", product: "crm", label: "B" };

describe("nextPage", () => {
  it("interleaves the sources newest first, across several pages, with no gap or repeat", async () => {
    const rows = {
      a: [59, 50, 40, 30, 20, 10].map((m) => entry("a", m)),
      b: [55, 45, 44, 43, 5].map((m) => entry("b", m)),
    };
    const fetch = server(rows);
    let states = initialStates([A, B]);
    const seen: string[] = [];
    for (let i = 0; i < 10; i++) {
      const page = await nextPage(states, 3, {}, fetch);
      seen.push(...page.entries.map((e) => e.id));
      states = page.states;
      if (!page.hasMore) break;
    }
    const expected = [...rows.a, ...rows.b]
      .sort((x, y) => Date.parse(y.at) - Date.parse(x.at))
      .map((e) => e.id);
    expect(seen).toEqual(expected);
  });

  it("passes the filters and the source's own cursor to every request", async () => {
    const fetch = server({ a: [3, 2, 1].map((m) => entry("a", m)) });
    const first = await nextPage(initialStates([A]), 2, { actor_id: "u1" }, fetch);
    await nextPage(first.states, 2, { actor_id: "u1" }, fetch);
    expect(fetch).toHaveBeenNthCalledWith(1, "a", { actor_id: "u1", limit: 2 });
    expect(fetch).toHaveBeenNthCalledWith(2, "a", { actor_id: "u1", limit: 2, cursor: "2" });
  });

  it("treats a product that is not active (402) as empty, not as a failure", async () => {
    const fetch = vi.fn(async (service: string): Promise<AuditPage> => {
      if (service === "b") throw new ApiError(402, "Not activated", null);
      return { service, product: "core", items: [entry("a", 1)], next_cursor: null };
    });
    const page = await nextPage(initialStates([A, B]), 10, {}, fetch);
    expect(page.entries.map((e) => e.id)).toEqual(["a-1"]);
    expect(page.states.every((s) => s.error === null)).toBe(true);
    expect(page.hasMore).toBe(false);
  });

  it("keeps the other sources when one fails, and records which one", async () => {
    const fetch = vi.fn(async (service: string): Promise<AuditPage> => {
      if (service === "b") throw new ApiError(500, "Boom", null);
      return { service, product: "core", items: [entry("a", 1)], next_cursor: null };
    });
    const page = await nextPage(initialStates([A, B]), 10, {}, fetch);
    expect(page.entries.map((e) => e.id)).toEqual(["a-1"]);
    expect(page.states.find((s) => s.service === "b")?.error).toBeInstanceOf(ApiError);
  });

  it("knows when every source refused the caller", async () => {
    const fetch = vi.fn(async (): Promise<AuditPage> => {
      throw new ApiError(403, "Missing permission", null);
    });
    const page = await nextPage(initialStates([A, B]), 10, {}, fetch);
    expect(everySourceRefused(page.states)).toBe(true);
  });
});

describe("activeSources", () => {
  it("always includes the platform and only the products the organisation has activated", () => {
    const services = activeSources(["crm"]).map((s) => s.service);
    const platform = ["account", "auth", "approval", "document", "subscription", "billing", "payment"];
    expect(services).toEqual(["account", "customer", "deal", "lead", ...platform.slice(1)]);
    expect(activeSources(null).map((s) => s.service)).toEqual(platform);
    expect(activeSources(["pulseboard"]).map((s) => s.service)).toEqual([
      ...platform,
      "project",
      "timesheet",
      "standup",
    ]);
  });
});

describe("describeAction", () => {
  it("turns an action code into words", () => {
    expect(describeAction("member.invited")).toBe("Member invited");
    expect(describeAction("deal.stage_changed")).toBe("Deal stage changed");
    expect(describeAction("auth.password_verified")).toBe("Signed in with a password");
  });
});
