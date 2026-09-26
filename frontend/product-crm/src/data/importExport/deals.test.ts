import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RowProblem } from "@opsgrids/client";
import type { ImportContext } from "@opsgrids/client";

import type { Deal } from "@/api/types";
import { getMySubjectRef } from "@/lib/subject";
import { dealImport } from "./deals";
import {
  OWNERS_WIRE,
  companyWire,
  contactWire,
  contextWith,
  pageOf,
  parseRow,
  present,
  stubGateway,
} from "./entityTestKit";

vi.mock("@/lib/subject", () => ({ getMySubjectRef: vi.fn() }));

const loadLookups = present(dealImport.loadLookups, "loadLookups");
const exported = present(dealImport.export, "export");

/** The organisation the fake gateway holds: two companies with one contact each. */
function gateway(overrides: { contacts?: (page: number) => unknown } = {}) {
  return stubGateway(({ url }) => {
    if (url.pathname.endsWith("/customers/owners")) return OWNERS_WIRE;
    if (url.pathname.endsWith("/customers")) {
      return pageOf([
        companyWire("comp-1", "Northwind Logistics"),
        companyWire("comp-2", "Cobalt Financial"),
      ]);
    }
    if (url.pathname.endsWith("/contacts")) {
      return (
        overrides.contacts?.(Number(url.searchParams.get("page"))) ??
        pageOf([
          contactWire("cont-1", "asha.rao@northwind.co", "comp-1"),
          contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2"),
        ])
      );
    }
    throw new Error(`unexpected request ${url.pathname}`);
  });
}

let ctx: ImportContext;

beforeEach(async () => {
  vi.mocked(getMySubjectRef).mockReturnValue("own-1");
  gateway();
  ctx = contextWith(await loadLookups(new AbortController().signal));
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const MINIMAL = {
  name: "Northwind fleet renewal",
  company: "Northwind Logistics",
  value: "3200000",
  closeDate: "2026-12-15",
};

function payloadFor(cells: Record<string, string | number>) {
  return dealImport.toPayload(parseRow(dealImport.columns, cells), ctx);
}

describe("deals: toPayload", () => {
  it("builds the create body from a full row, resolving company, contact and owner", () => {
    expect(
      payloadFor({
        ...MINIMAL,
        value: "3200000.50",
        currency: "inr",
        contact: "Asha.Rao@Northwind.co",
        owner: "grace@example.com",
        stage: "Closed Won",
        probability: 100,
        source: "Referral",
      }),
    ).toEqual({
      name: "Northwind fleet renewal",
      company_id: "comp-1",
      owner_id: "own-2",
      value_minor: 320000050,
      close_date: "2026-12-15",
      currency: "INR",
      stage: "closed_won",
      probability: 100,
      primary_contact_id: "cont-1",
      source: "Referral",
    });
  });

  it("defaults what the New deal form defaults: stage Lead, INR, the caller as owner, no contact", () => {
    const payload = payloadFor(MINIMAL);
    expect(payload).toEqual({
      name: "Northwind fleet renewal",
      company_id: "comp-1",
      owner_id: "own-1",
      value_minor: 320000000,
      close_date: "2026-12-15",
      currency: "INR",
      stage: "lead",
      source: "",
    });
    expect("probability" in payload).toBe(false);
    expect("primary_contact_id" in payload).toBe(false);
  });

  it("sends a probability of zero rather than treating it as blank", () => {
    expect(payloadFor({ ...MINIMAL, stage: "Closed Lost", probability: 0 }).probability).toBe(0);
  });

  it("refuses a company that does not exist, because the deal service would store the orphan", () => {
    expect(() => payloadFor({ ...MINIMAL, company: "Nowhere Ltd" })).toThrow(RowProblem);
    expect(() => payloadFor({ ...MINIMAL, company: "Nowhere Ltd" })).toThrow(
      /No company is named "Nowhere Ltd"/,
    );
  });

  it("refuses a primary contact who does not exist", () => {
    expect(() => payloadFor({ ...MINIMAL, contact: "ghost@nowhere.example" })).toThrow(
      /No contact has the email "ghost@nowhere.example"/,
    );
  });

  it("refuses a primary contact who belongs to another company", () => {
    expect(() => payloadFor({ ...MINIMAL, contact: "daniel.okafor@cobaltfin.com" })).toThrow(
      /daniel.okafor@cobaltfin.com is not a contact of Northwind Logistics/,
    );
  });

  it("refuses a blank owner when the account has no rep record to fall back on", () => {
    vi.mocked(getMySubjectRef).mockReturnValue(null);
    expect(() => payloadFor(MINIMAL)).toThrow(/Owner email is blank/);
  });
});

describe("deals: loadLookups", () => {
  it("reads every page of contacts, so a contact past the first 200 still resolves", async () => {
    const firstPage = Array.from({ length: 200 }, (_, i) =>
      contactWire(`cont-${i + 1}`, `person${i + 1}@northwind.co`, "comp-1"),
    );
    const calls = gateway({
      contacts: (page) =>
        page === 1
          ? pageOf(firstPage, 1, 200, 201)
          : pageOf([contactWire("cont-201", "last.person@northwind.co", "comp-1")], 2, 200, 201),
    });

    const lookups = await loadLookups(new AbortController().signal);

    const contactPages = calls.filter((c) => c.url.pathname === "/api/v1/contacts");
    expect(contactPages.map((c) => c.url.searchParams.get("page"))).toEqual(["1", "2"]);
    expect(lookups.contacts?.resolve("last.person@northwind.co")).toBeDefined();
    expect(lookups.companies?.resolve("cobalt financial")).toBeDefined();
    expect(lookups.owners?.resolve("sofia@example.com")).toBe("own-1");
  });
});

describe("deals: notes", () => {
  it("tells the user that deal names are unique across the whole organisation", () => {
    expect(dealImport.notes?.join(" ")).toMatch(/unique across the whole organisation/);
  });
});

describe("deals: create", () => {
  it("POSTs the payload to /deals and returns the new id", async () => {
    const calls = stubGateway(
      () => ({
        id: "deal-9",
        name: "Northwind fleet renewal",
        company_id: "comp-1",
        primary_contact_id: null,
        value_minor: 320000000,
        currency: "INR",
        stage: "lead",
        owner_id: "own-1",
        probability: 20,
        close_date: "2026-12-15",
        created_at: "2026-09-27T09:00:00Z",
        last_activity_at: null,
        source: "",
      }),
      201,
    );
    const payload = payloadFor(MINIMAL);
    const created = await dealImport.create(payload, ctx);
    expect(calls[0]?.url.pathname).toBe("/api/v1/deals");
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toEqual(payload);
    expect(created).toMatchObject({ id: "deal-9" });
  });
});

describe("deals: export", () => {
  const DEAL: Deal = {
    id: "deal-1",
    name: "Northwind fleet renewal",
    company_id: "comp-1",
    primary_contact_id: "cont-1",
    value_minor: 320000000,
    currency: "INR",
    stage: "closed_won",
    owner_id: "own-2",
    probability: 100,
    close_date: "2026-12-15",
    created_at: "2026-08-01T09:00:00Z",
    last_activity_at: "2026-09-01T09:00:00Z",
    source: "Referral",
  };

  it("pages the pipeline with the screen's search, stage and owner, whatever view is showing", async () => {
    const calls = stubGateway(() => pageOf([], 1, 200));
    const controller = new AbortController();
    await exported.fetchPage({
      page: 3,
      size: 200,
      filters: { search: "renewal", stage: "proposal", ownerId: "own-2" },
      signal: controller.signal,
    });
    const params = calls[0]?.url.searchParams;
    expect(calls[0]?.url.pathname).toBe("/api/v1/deals");
    expect(params?.get("q")).toBe("renewal");
    expect(params?.get("stage")).toBe("proposal");
    expect(params?.get("owner_id")).toBe("own-2");
    expect(params?.get("page")).toBe("3");
    expect(params?.get("size")).toBe("200");
    expect(calls[0]?.signal).toBe(controller.signal);
  });

  it("leaves the stage and owner filters off when the screen shows every stage and owner", async () => {
    const calls = stubGateway(() => pageOf([]));
    await exported.fetchPage({
      page: 1,
      size: 200,
      filters: { search: "", stage: "all", ownerId: "all" },
      signal: new AbortController().signal,
    });
    const params = calls[0]?.url.searchParams;
    expect(params?.has("stage")).toBe(false);
    expect(params?.has("owner_id")).toBe(false);
    expect(params?.has("q")).toBe(false);
  });

  it("writes one row per deal, with names for ids and the value in major units", () => {
    const row = exported.toRow(DEAL, ctx.lookups);
    expect(row).toEqual({
      name: "Northwind fleet renewal",
      company: "Northwind Logistics",
      contact: "asha.rao@northwind.co",
      owner: "Grace@Example.com",
      value: "3200000.00",
      currency: "INR",
      closeDate: "2026-12-15",
      stage: "Closed Won",
      probability: 100,
      source: "Referral",
      lastActivity: "2026-09-01T09:00:00Z",
      id: "deal-1",
      createdAt: "2026-08-01T09:00:00Z",
    });
    expect(Object.keys(row).sort()).toEqual(dealImport.columns.map((c) => c.key).sort());
  });

  it("writes a deal with no primary contact as blank", () => {
    expect(exported.toRow({ ...DEAL, primary_contact_id: null }, ctx.lookups).contact).toBeNull();
  });

  it("still names the primary contact when they have since moved to another company", () => {
    expect(exported.toRow({ ...DEAL, primary_contact_id: "cont-2" }, ctx.lookups).contact).toBe(
      "daniel.okafor@cobaltfin.com",
    );
  });
});
