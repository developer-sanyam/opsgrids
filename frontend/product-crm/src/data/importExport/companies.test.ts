import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, makeLookup } from "@opsgrids/client";
import type { LookupSet, ParsedValue } from "@opsgrids/client";

import type { Company } from "@/api/types";
import { getMySubjectRef } from "@/lib/subject";
import { companyImport } from "./companies";
import {
  OWNERS_WIRE,
  companyWire,
  contextWith,
  pageOf,
  parsedRow,
  present,
  stubGateway,
} from "./entityTestKit";

vi.mock("@/lib/subject", () => ({ getMySubjectRef: vi.fn() }));

const LOOKUPS: LookupSet = {
  owners: makeLookup([
    { label: "sofia@example.com", value: "own-1" },
    { label: "Grace@Example.com", value: "own-2" },
  ]),
};

const ctx = contextWith(LOOKUPS);

beforeEach(() => {
  vi.mocked(getMySubjectRef).mockReturnValue("own-1");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const loadLookups = present(companyImport.loadLookups, "loadLookups");
const exported = present(companyImport.export, "export");

function payloadFor(cells: Record<string, ParsedValue>) {
  return companyImport.toPayload(parsedRow(companyImport.columns, cells), ctx);
}

describe("companies: toPayload", () => {
  it("builds the create body from a full row, with revenue in integer minor units", () => {
    expect(
      payloadFor({
        name: "Northwind Logistics",
        owner: "GRACE@example.com",
        domain: "northwind.co",
        industry: "Logistics",
        sizeBand: "501-1000",
        city: "Mumbai",
        country: "India",
        status: "customer",
        annualRevenue: 8400000050,
        currency: "usd",
      }),
    ).toEqual({
      name: "Northwind Logistics",
      owner_id: "own-2",
      domain: "northwind.co",
      industry: "Logistics",
      size_band: "501-1000",
      city: "Mumbai",
      country: "India",
      status: "customer",
      annual_revenue_minor: 8400000050,
      currency: "USD",
    });
  });

  it("defaults what the New company form defaults: prospect, no revenue, INR", () => {
    expect(payloadFor({ name: "Solstice Energy" })).toEqual({
      name: "Solstice Energy",
      owner_id: "own-1",
      domain: "",
      industry: "",
      size_band: "",
      city: "",
      country: "",
      status: "prospect",
      annual_revenue_minor: 0,
      currency: "INR",
    });
  });

  it("gives a blank owner to the signed-in rep", () => {
    vi.mocked(getMySubjectRef).mockReturnValue("own-9");
    expect(payloadFor({ name: "Solstice Energy" }).owner_id).toBe("own-9");
  });

  it("refuses a blank owner when the account has no rep record to fall back on", () => {
    vi.mocked(getMySubjectRef).mockReturnValue(null);
    expect(() => payloadFor({ name: "Solstice Energy" })).toThrow(RowProblem);
    expect(() => payloadFor({ name: "Solstice Energy" })).toThrow(/Owner email is blank/);
  });

  it("names the email when no active team member has it", () => {
    expect(() => payloadFor({ name: "Solstice Energy", owner: "nobody@example.com" })).toThrow(
      /No active team member has the email "nobody@example.com"/,
    );
  });

  it("refuses to guess between two team members with the same email", () => {
    const twins = contextWith({
      owners: makeLookup([
        { label: "twin@example.com", value: "own-4" },
        { label: "Twin@example.com", value: "own-5" },
      ]),
    });
    const row = parsedRow(companyImport.columns, { name: "Solstice Energy", owner: "twin@example.com" });
    expect(() => companyImport.toPayload(row, twins)).toThrow(/More than one team member/);
  });

  it("does not pre-empt the server for an owner the caller may not assign", () => {
    // Naming another rep is sent as written: the server answers 403 for a caller limited to their
    // own book, and that sentence is reported on the row.
    expect(payloadFor({ name: "Solstice Energy", owner: "grace@example.com" }).owner_id).toBe("own-2");
  });
});

describe("companies: loadLookups", () => {
  it("resolves active team members by email, ignoring case, and leaves out deactivated ones", async () => {
    stubGateway(() => OWNERS_WIRE);
    const { owners } = await loadLookups(new AbortController().signal);
    expect(owners?.labels).toEqual(["sofia@example.com", "Grace@Example.com"]);
    expect(owners?.resolve("GRACE@EXAMPLE.COM")).toBe("own-2");
    expect(owners?.resolve("former@example.com")).toBeUndefined();
  });

  it("hands the dialog's signal to the request, so closing the dialog cancels the load", async () => {
    const calls = stubGateway(() => OWNERS_WIRE);
    const controller = new AbortController();
    await loadLookups(controller.signal);
    expect(calls[0]?.signal).toBe(controller.signal);
  });
});

describe("companies: create", () => {
  it("POSTs the payload to /customers and returns the new id", async () => {
    const calls = stubGateway(() => companyWire("comp-9", "Solstice Energy"), 201);
    const payload = payloadFor({ name: "Solstice Energy" });
    const created = await companyImport.create(payload, ctx);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url.pathname).toBe("/api/v1/customers");
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toEqual(payload);
    expect(created).toMatchObject({ id: "comp-9" });
  });

  it("passes the server's own sentence through when it refuses an owner", async () => {
    stubGateway(
      () => ({ title: "Forbidden", detail: "You can only assign companies you own to yourself", status: 403 }),
      403,
    );
    await expect(companyImport.create(payloadFor({ name: "Solstice Energy", owner: "grace@example.com" }), ctx))
      .rejects.toMatchObject({ status: 403, message: "You can only assign companies you own to yourself" });
  });

  it("lets a duplicate name surface as a 409 for the engine to report as skipped", async () => {
    stubGateway(
      () => ({ title: "Conflict", detail: "A company with that name already exists", status: 409 }),
      409,
    );
    const failure = await companyImport.create(payloadFor({ name: "Northwind Logistics" }), ctx).catch(
      (error: unknown) => error,
    );
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409 });
  });
});

describe("companies: export", () => {
  const COMPANY: Company = {
    id: "comp-1",
    name: "Northwind Logistics",
    domain: "northwind.co",
    industry: "Logistics",
    size_band: "501-1000",
    city: "Mumbai",
    country: "India",
    status: "customer",
    owner_id: "own-2",
    annual_revenue_minor: 8400000050,
    currency: "INR",
    created_at: "2024-02-11T09:00:00Z",
  };

  it("pages the list with the screen's current search and status", async () => {
    const calls = stubGateway(() => pageOf([companyWire("comp-1", "Northwind Logistics")], 2, 200, 201));
    const controller = new AbortController();
    const page = await exported.fetchPage({
      page: 2,
      size: 200,
      filters: { search: "north", status: "customer" },
      signal: controller.signal,
    });
    const params = calls[0]?.url.searchParams;
    expect(calls[0]?.url.pathname).toBe("/api/v1/customers");
    expect(params?.get("q")).toBe("north");
    expect(params?.get("status")).toBe("customer");
    expect(params?.get("page")).toBe("2");
    expect(params?.get("size")).toBe("200");
    expect(calls[0]?.signal).toBe(controller.signal);
    expect(page).toMatchObject({ total: 201, pages: 2 });
    expect(page.items.map((c) => c.name)).toEqual(["Northwind Logistics"]);
  });

  it("leaves the status filter off when the screen shows every status", async () => {
    const calls = stubGateway(() => pageOf([]));
    await exported.fetchPage({
      page: 1,
      size: 200,
      filters: { search: "", status: "all" },
      signal: new AbortController().signal,
    });
    expect(calls[0]?.url.searchParams.has("status")).toBe(false);
    expect(calls[0]?.url.searchParams.has("q")).toBe(false);
  });

  it("writes one row per company, with the owner as an email and revenue in major units", () => {
    const row = exported.toRow(COMPANY, LOOKUPS);
    expect(row).toEqual({
      name: "Northwind Logistics",
      owner: "Grace@Example.com",
      domain: "northwind.co",
      industry: "Logistics",
      sizeBand: "501-1000",
      city: "Mumbai",
      country: "India",
      status: "Customer",
      annualRevenue: "84000000.50",
      currency: "INR",
      id: "comp-1",
      createdAt: "2024-02-11T09:00:00Z",
    });
    expect(Object.keys(row).sort()).toEqual(companyImport.columns.map((c) => c.key).sort());
  });

  it("leaves the owner blank when that rep is no longer in the team list", () => {
    expect(exported.toRow({ ...COMPANY, owner_id: "own-gone" }, LOOKUPS).owner).toBeNull();
  });

  it("writes a zero-decimal currency without a fraction", () => {
    const row = exported.toRow(
      { ...COMPANY, currency: "JPY", annual_revenue_minor: 500 },
      LOOKUPS,
    );
    expect(row.annualRevenue).toBe("500");
  });
});
