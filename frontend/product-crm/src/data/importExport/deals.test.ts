import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RowProblem } from "@opsgrids/client";
import type { ImportContext, ParsedValue } from "@opsgrids/client";

import type { Deal } from "@/api/types";
import { getMySubjectRef } from "@/lib/subject";
import { dealImport } from "./deals";
import {
  OWNERS_WIRE,
  companyWire,
  contactWire,
  contextWith,
  dealWire,
  pageOf,
  parsedRow,
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
  value: 320000000,
  closeDate: "2026-12-15",
};

function payloadFor(cells: Record<string, ParsedValue>) {
  return dealImport.toPayload(parsedRow(dealImport.columns, cells), ctx);
}

describe("deals: toPayload", () => {
  it("builds the create body from a full row, resolving company, contact and owner", () => {
    expect(
      payloadFor({
        ...MINIMAL,
        value: 320000050,
        currency: "inr",
        contact: "Asha.Rao@Northwind.co",
        owner: "grace@example.com",
        stage: "closed_won",
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
    expect(payloadFor({ ...MINIMAL, stage: "closed_lost", probability: 0 }).probability).toBe(0);
  });

  it("refuses a primary contact who belongs to another company", () => {
    expect(() => payloadFor({ ...MINIMAL, contact: "daniel.okafor@cobaltfin.com" })).toThrow(RowProblem);
    expect(() => payloadFor({ ...MINIMAL, contact: "daniel.okafor@cobaltfin.com" })).toThrow(
      "daniel.okafor@cobaltfin.com is not a contact of Northwind Logistics. Pick one of that company's contacts.",
    );
  });

  it("refuses a blank owner when the signed-in account is not an active rep", async () => {
    // `self` is decided when the lookups load, so load them as each kind of account.
    for (const sref of [null, "own-3"]) {
      vi.mocked(getMySubjectRef).mockReturnValue(sref);
      gateway();
      const asThem = contextWith(await loadLookups(new AbortController().signal));
      vi.unstubAllGlobals();
      expect(() => dealImport.toPayload(parsedRow(dealImport.columns, MINIMAL), asThem)).toThrow(RowProblem);
    }
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
    expect(lookups.contacts?.resolve("last.person@northwind.co")).toBe("cont-201");
    expect(lookups.contactCompanies?.resolve("last.person@northwind.co")).toBe("comp-1");
    expect(lookups.companies?.resolve("cobalt financial")).toBeDefined();
    expect(lookups.owners?.resolve("sofia@example.com")).toBe("own-1");
  });

  it("also loads every team member, deactivated ones too, for the export only", async () => {
    gateway();
    const lookups = await loadLookups(new AbortController().signal);
    expect(lookups.ownersAll?.labelOf("own-3")).toBe("former@example.com");
  });
});

describe("deals: what the user is told about names", () => {
  it("says deal names are unique across the whole organisation", () => {
    expect(dealImport.notes?.join(" ")).toMatch(/unique across the whole organisation/);
  });

  it("says names are matched exactly, capitals included, on the column and in the notes", () => {
    const help = dealImport.columns.find((c) => c.key === "name")?.help;
    expect(help).toMatch(/matched exactly, capitals included/);
    expect(help).toContain("'Q4 Renewal' and 'Q4 renewal' are different deals");
    expect(dealImport.notes?.join(" ")).toMatch(/matched exactly, capitals included/);
  });

  it("says an update never takes a name that differs only in capitals for the deal it resembles", () => {
    // Exact matching, so a row in other capitals is refused (by the engine), not applied to 'Q4 renewal'.
    const help = dealImport.columns.find((c) => c.key === "name")?.help;
    expect(help).toContain("a name that differs from an existing deal's only in capitals is refused");
    expect(dealImport.notes?.join(" ")).toContain(
      "a name that differs from an existing deal's only in capitals is refused, so a row never changes a deal it only resembles",
    );
  });

  it("says a stage move resets the probability, and what to do to keep a figure", () => {
    const notes = dealImport.notes?.join(" ") ?? "";
    expect(notes).toContain("resets Probability to the new stage's usual figure, as dragging it on the board does");
    expect(notes).toContain("'Probability (reset to 70%)'");
    expect(notes).toContain("move the stage first and change Probability in a second file");
    const help = dealImport.columns.find((c) => c.key === "probability")?.help;
    expect(help).toContain("a blank cell keeps the current one, except in a row that moves the stage");
  });

  it("warns once that closing a deal cannot be undone, and says what the preview calls it", () => {
    const notes = dealImport.notes ?? [];
    const warnings = notes.filter((note) => note.includes("Moving a deal to Closed Won or Closed Lost cannot be undone"));
    expect(warnings).toHaveLength(1);
    expect(warnings.join(" ")).toContain("'Stage (closes the deal)'");
    expect(warnings.join(" ")).toContain("a closed deal cannot change stage again");
  });
});

describe("deals: create", () => {
  it("POSTs the payload to /deals and returns the new id", async () => {
    const calls = stubGateway(() => dealWire("deal-9", "Northwind fleet renewal"), 201);
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

  it("names a deactivated rep as the owner, so the export says who owned the deal", () => {
    expect(exported.toRow({ ...DEAL, owner_id: "own-3" }, ctx.lookups).owner).toBe("former@example.com");
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
