/**
 * The console's data client, in both of its modes.
 *
 * **Fixtures** are what the console renders in development, and what every screen test here runs
 * on — so the filtering and composition they do are behaviour a screen depends on, pinned. **Live**
 * is what a production build does, and what has to be right about it is the door: the route and
 * method of each call, the operator token it carries, and that an answer in a shape the console does
 * not read is named as such rather than handed to a screen that would throw.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import { json, operatorToken, problem, stubFetch } from "@/testing";
import { OPERATOR_SESSION_KEY } from "@/auth/keys";
import { CREDIT_LEDGER, ORGANIZATIONS, SUBSCRIPTIONS, organizationDetail } from "./fixtures";
import * as fixtures from "./client";
import { ContractMismatchError } from "./shape";

describe("fixtures — organizations", () => {
  it("returns every organization when nothing narrows it", async () => {
    const page = await fixtures.listOrganizations();
    expect(page.items).toHaveLength(ORGANIZATIONS.length);
    expect(page.total).toBe(ORGANIZATIONS.length);
  });

  it("narrows by name, slug, product and region, ignoring case and the space around it", async () => {
    expect((await fixtures.listOrganizations({ search: "  NORTHWIND " })).items.map((o) => o.name)).toEqual(["Northwind Metals"]);
    expect((await fixtures.listOrganizations({ search: "kaveri-steel" })).items.map((o) => o.name)).toEqual(["Kaveri Steel"]);
    expect((await fixtures.listOrganizations({ search: "chennai" })).items.map((o) => o.name)).toEqual(["Sundaram Auctioneers"]);
    expect((await fixtures.listOrganizations({ search: "Trading Desk" })).items.length).toBeGreaterThan(0);
  });

  it("narrows by lifecycle status, and 'all' is no narrowing", async () => {
    const suspended = await fixtures.listOrganizations({ status: "suspended" });
    expect(suspended.items.map((o) => o.name).sort()).toEqual(["Sahara Scrap Co", "Vertex Mining"]);
    expect((await fixtures.listOrganizations({ status: "all" })).items).toHaveLength(ORGANIZATIONS.length);
  });

  it("narrows to bespoke pricing, and its absence is no narrowing", async () => {
    const bespoke = await fixtures.listOrganizations({ bespoke: true });
    expect(bespoke.items.every((o) => o.is_bespoke)).toBe(true);
    expect(bespoke.items.length).toBeGreaterThan(0);
    expect((await fixtures.listOrganizations({})).items).toHaveLength(ORGANIZATIONS.length);
  });

  it("combines every narrowing at once", async () => {
    const page = await fixtures.listOrganizations({ search: "steel", status: "active", bespoke: false });
    expect(page.items.map((o) => o.name)).toEqual(["Kaveri Steel"]);
  });
});

describe("fixtures — one organization", () => {
  it("composes the account's subscriptions, entitlements, usage, invoices and ledger, with the balance a SUM over what is in force", async () => {
    const detail = await fixtures.getOrganization("org_e5b440");
    expect(detail.organization.name).toBe("Deccan Commodities");
    expect(detail.subscriptions.length).toBeGreaterThan(0);
    expect(detail.entitlements).toHaveLength(detail.subscriptions.length);
    expect(detail).toEqual(organizationDetail("org_e5b440"));
  });

  it("says an unknown organization is not found, as a 404 the screen can tell from a fault", async () => {
    const failure = await fixtures.getOrganization("org_nope").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(404);
    expect((failure as ApiError).message).toBe("No such organization");
  });
});

describe("fixtures — subscriptions, ledger, audit", () => {
  it("narrows subscriptions by organization, product or plan, and by state", async () => {
    expect((await fixtures.listSubscriptions({ search: "kaveri" })).items).toHaveLength(
      SUBSCRIPTIONS.filter((s) => s.organization_name === "Kaveri Steel").length,
    );
    const trials = await fixtures.listSubscriptions({ state: "trial" });
    expect(trials.items.every((s) => s.state === "trial")).toBe(true);
    expect((await fixtures.listSubscriptions({ state: "all" })).items).toHaveLength(SUBSCRIPTIONS.length);
  });

  it("returns the ledger newest first, and narrows to comp entries", async () => {
    const all = await fixtures.listLedger();
    const dates = all.items.map((e) => e.created_at);
    expect(dates).toEqual([...dates].sort((a, b) => b.localeCompare(a)));
    const comp = await fixtures.listLedger({ compOnly: true });
    expect(comp.items).toHaveLength(CREDIT_LEDGER.filter((e) => e.is_comp).length);
    expect(comp.items.every((e) => e.is_comp)).toBe(true);
  });

  it("narrows the ledger by organization or reason", async () => {
    expect((await fixtures.listLedger({ search: "goodwill" })).items.length).toBeGreaterThan(0);
    expect((await fixtures.listLedger({ search: "zzzz" })).items).toEqual([]);
  });

  it("narrows the audit log by text and by action, across operator, organization and summary", async () => {
    expect((await fixtures.listAudit({ search: "rahul" })).items.every((a) => a.actor === "Rahul Menon" || a.summary.toLowerCase().includes("rahul"))).toBe(true);
    const suspends = await fixtures.listAudit({ action: "override.suspend" });
    expect(suspends.items.every((a) => a.action === "override.suspend")).toBe(true);
    expect((await fixtures.listAudit({ action: "all" })).items.length).toBeGreaterThan(suspends.items.length);
  });

  it("accepts an override, in words that say nothing was sent anywhere", async () => {
    const result = await fixtures.postOverride({ kind: "suspend", organization_id: "org_a1f09c", reason: "test" });
    expect(result.status).toBe("accepted");
    expect(result.summary).toContain("fixtures mode");
    expect(result.audit_id).toMatch(/^aud_/);
  });
});

/**
 * A fresh copy of the client with fixtures off, and a session holding an operator token — in one
 * module graph, because the session store is module state and must be the one the client reads.
 */
async function live() {
  vi.resetModules();
  vi.stubEnv("VITE_USE_FIXTURES", "false");
  const sdk = await import("@opsgrids/client");
  sdk.configureSession(OPERATOR_SESSION_KEY);
  const token = operatorToken();
  sdk.setAccessToken(token);
  const client = await import("./client");
  const shape = await import("./shape");
  return { client, sdk, token, shape };
}

describe("live — what goes out", () => {
  it("reads the platform metrics from the operator route, with the operator's token", async () => {
    const { client, token, shape } = await live();
    const { calls } = stubFetch({
      "GET /admin/metrics/overview": () => json({ nope: true }),
    });

    await expect(client.getOverview()).rejects.toBeInstanceOf(shape.ContractMismatchError);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.path).toBe("/admin/metrics/overview");
    expect(calls[0]?.headers.Authorization).toBe(`Bearer ${token}`);
  });

  it("asks for organizations with the filters the operator set, and none it did not", async () => {
    const { client } = await live();
    const { calls } = stubFetch({
      "GET /admin/organizations": () => json({ items: [], total: 0 }),
    });

    await client.listOrganizations({ search: "  acme ", status: "active", bespoke: true });
    await client.listOrganizations({ status: "all" });

    expect(calls[0]?.path).toBe("/admin/organizations?q=acme&status=active&bespoke=true");
    // Nothing to ask means no query string at all, not a dangling `?`.
    expect(calls[1]?.path).toBe("/admin/organizations");
  });

  it("names one organization by its id, encoded so an id cannot change the route", async () => {
    const { client } = await live();
    const { calls } = stubFetch({
      "GET /admin/organizations/a%2Fb%3Fc": () => json({ organization: {}, subscriptions: [], entitlements: [], usage: [], invoices: [], ledger: [], comp_balance_minor: 0 }),
    });

    await client.getOrganization("a/b?c");

    expect(calls[0]?.path).toBe("/admin/organizations/a%2Fb%3Fc");
  });

  it("refuses an id that would walk out of the operator surface, before any request", async () => {
    const { client } = await live();
    const { calls } = stubFetch({});

    await expect(client.getOrganization("..")).rejects.toThrow(/only calls \/admin and \/auth\/operator/);
    expect(calls).toHaveLength(0);
  });

  it("asks for subscriptions, the catalog, the ledger and the audit log on their own routes", async () => {
    const { client } = await live();
    const { calls } = stubFetch({
      "GET /admin/subscriptions": () => json({ items: [], total: 0 }),
      "GET /admin/catalog": () => json({ products: [], plans: [], versions: [] }),
      "GET /admin/credit-ledger": () => json({ items: [], total: 0, page: 1, size: 25, pages: 0 }),
      "GET /admin/audit": () => json({ items: [], total: 0 }),
    });

    await client.listSubscriptions({ search: "kaveri", state: "trial" });
    await client.getCatalog();
    await client.listLedger({ search: "goodwill", compOnly: true });
    await client.listAudit({ search: "rahul", action: "override.suspend" });

    expect(calls.map((c) => c.path)).toEqual([
      "/admin/subscriptions?q=kaveri&state=trial",
      "/admin/catalog",
      "/admin/credit-ledger?q=goodwill&comp=true",
      "/admin/audit?q=rahul&action=override.suspend",
    ]);
  });

  it("posts an override to the operator route with the body the operator's action built", async () => {
    const { client } = await live();
    const { calls } = stubFetch({
      "POST /admin/overrides": () => json({ action: "suspend", organization_id: "org_1", delegated_to: "subscription", result: {} }),
    });

    await client.postOverride({ kind: "suspend", organization_id: "org_1", reason: "Non-payment" });

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toEqual({ kind: "suspend", organization_id: "org_1", reason: "Non-payment" });
  });
});

describe("live — what comes back", () => {
  it("hands a screen an answer that has the shape it reads", async () => {
    const { client } = await live();
    stubFetch({
      "GET /admin/audit": () => json({ items: [{ audit_id: "a" }], total: 1 }),
    });
    await expect(client.listAudit()).resolves.toEqual({ items: [{ audit_id: "a" }], total: 1 });
  });

  it("names the operator API's own list shape as not the console's, instead of crashing a render on it", async () => {
    const { client, shape } = await live();
    stubFetch({
      "GET /admin/organizations": () => json({ organizations: [], metrics: {} }),
    });
    const failure = await client.listOrganizations().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(shape.ContractMismatchError);
    expect((failure as ContractMismatchError).message).toContain("/admin/organizations");
    expect((failure as ContractMismatchError).message).toContain("`items`");
  });

  it("checks each ledger entry, not only the page around them", async () => {
    const { client, shape } = await live();
    stubFetch({
      // The billing service's own entry: `id` and `entry_type`, not `entry_id` and `kind`.
      "GET /admin/credit-ledger": () =>
        json({ items: [{ id: "x", entry_type: "grant" }], total: 1, page: 1, size: 25, pages: 1 }),
    });
    const failure = await client.listLedger().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(shape.ContractMismatchError);
    expect((failure as ContractMismatchError).missing).toContain("entry_id");
  });

  it("passes a server refusal through as itself, so the screen can tell a role from a fault", async () => {
    const { client } = await live();
    stubFetch({
      "GET /admin/usage": () => problem(403, "nope", { type: "forbidden" }),
      "GET /admin/subscriptions": () =>
        problem(403, "This action needs one of these operator roles: billing_ops, owner", { type: "forbidden" }),
    });
    await expect(client.listSubscriptions()).rejects.toMatchObject({ status: 403 });
  });

  it("reads an accepted override without turning an override that took effect into an error", async () => {
    const { client } = await live();
    stubFetch({
      "POST /admin/overrides": () =>
        json({ action: "comp_grant", organization_id: "org_1", delegated_to: "billing", result: { id: "x" } }),
    });
    const result = await client.postOverride({
      kind: "comp_grant",
      organization_id: "org_1",
      amount_minor: 100,
      currency: "INR",
      is_comp: true,
      reason: "r",
      valid_until: null,
    });
    expect(result).toEqual({
      status: "accepted",
      summary: "The comp_grant override was delegated to billing.",
      audit_id: null,
    });
  });

  it("reads an accepted override that names its own summary and audit row", async () => {
    const { client } = await live();
    stubFetch({
      "POST /admin/overrides": () => json({ summary: "Suspended Northwind", audit_id: "aud_9" }),
    });
    const result = await client.postOverride({ kind: "suspend", organization_id: "org_1", reason: "r" });
    expect(result).toEqual({ status: "accepted", summary: "Suspended Northwind", audit_id: "aud_9" });
  });

  it("does not read an unrecognisable answer to a write as a failure", async () => {
    const { client } = await live();
    stubFetch({ "POST /admin/overrides": () => new Response(null, { status: 204 }) });
    const result = await client.postOverride({ kind: "suspend", organization_id: "org_1", reason: "r" });
    expect(result.status).toBe("accepted");
    expect(result.audit_id).toBeNull();
  });
});
