import { afterEach, describe, expect, it, vi } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import type { Lead } from "@/api/types";
import { leadImport } from "./leads";
import { OWNERS_WIRE, contextWith, pageOf, parseRow, present, stubGateway } from "./entityTestKit";

const LOOKUPS: LookupSet = {
  owners: makeLookup([
    { label: "sofia@example.com", value: "own-1" },
    { label: "Grace@Example.com", value: "own-2" },
  ]),
};

const ctx = contextWith(LOOKUPS);
const loadLookups = present(leadImport.loadLookups, "loadLookups");
const exported = present(leadImport.export, "export");

afterEach(() => {
  vi.unstubAllGlobals();
});

function payloadFor(cells: Record<string, string | number>) {
  return leadImport.toPayload(parseRow(leadImport.columns, cells), ctx);
}

const LEAD_WIRE = {
  id: "lead-1",
  full_name: "Meera Iyer",
  company: "Halcyon Textiles",
  email: "m•••@h•••.in",
  phone: "+•• ••••• ••345",
  source: "import",
  status: "new",
  owner_id: "own-2",
  notes: "Asked for a demo.",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-09-20T09:00:00Z",
};

describe("leads: toPayload", () => {
  it("builds the create body from a full row, always from the source Import", () => {
    expect(
      payloadFor({
        fullName: "Meera Iyer",
        company: "Halcyon Textiles",
        email: "Meera.Iyer@Halcyontextiles.in",
        phone: "+91 90000 12345",
        owner: "grace@example.com",
        notes: "Asked for a demo.",
      }),
    ).toEqual({
      full_name: "Meera Iyer",
      company: "Halcyon Textiles",
      email: "meera.iyer@halcyontextiles.in",
      phone: "+919000012345",
      source: "import",
      owner_id: "own-2",
      notes: "Asked for a demo.",
    });
  });

  it("leaves owner_id out for a blank owner, so the round-robin rule assigns the lead", () => {
    const payload = payloadFor({ fullName: "Meera Iyer" });
    expect(payload).toEqual({
      full_name: "Meera Iyer",
      company: null,
      email: null,
      phone: null,
      source: "import",
      notes: "",
    });
    expect("owner_id" in payload).toBe(false);
  });

  it("refuses an owner who is not an active team member, naming the email", () => {
    expect(() => payloadFor({ fullName: "Meera Iyer", owner: "former@example.com" })).toThrow(RowProblem);
    expect(() => payloadFor({ fullName: "Meera Iyer", owner: "former@example.com" })).toThrow(
      /No active team member has the email "former@example.com"/,
    );
  });

  it("refuses a phone number without a country code", () => {
    expect(() => payloadFor({ fullName: "Meera Iyer", phone: "090000 12345" })).toThrow(/country code/);
  });
});

describe("leads: loadLookups", () => {
  it("offers only active team members, by email", async () => {
    stubGateway(() => OWNERS_WIRE);
    const { owners } = await loadLookups(new AbortController().signal);
    expect(owners?.labels).toEqual(["sofia@example.com", "Grace@Example.com"]);
  });
});

describe("leads: create", () => {
  it("POSTs the payload to /leads and returns the new id", async () => {
    const calls = stubGateway(() => LEAD_WIRE, 201);
    const payload = payloadFor({ fullName: "Meera Iyer", email: "meera@halcyontextiles.in" });
    const created = await leadImport.create(payload, ctx);
    expect(calls[0]?.url.pathname).toBe("/api/v1/leads");
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toEqual(payload);
    expect(created).toMatchObject({ id: "lead-1" });
  });
});

describe("leads: export", () => {
  const LEAD: Lead = {
    id: "lead-1",
    full_name: "Meera Iyer",
    company: "Halcyon Textiles",
    email: "m•••@h•••.in",
    phone: "+•• ••••• ••345",
    source: "import",
    status: "working",
    owner_id: "own-2",
    notes: "Asked for a demo.",
    converted_company_id: null,
    converted_contact_id: null,
    converted_deal_id: null,
    converted_at: null,
    created_at: "2026-09-20T09:00:00Z",
  };

  it("pages the list with the screen's current status", async () => {
    const calls = stubGateway(() => pageOf([LEAD_WIRE], 1, 200));
    const controller = new AbortController();
    const page = await exported.fetchPage({
      page: 1,
      size: 200,
      filters: { status: "working" },
      signal: controller.signal,
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url.pathname).toBe("/api/v1/leads");
    expect(calls[0]?.url.searchParams.get("status")).toBe("working");
    expect(calls[0]?.url.searchParams.get("size")).toBe("200");
    expect(calls[0]?.signal).toBe(controller.signal);
    expect(page.items).toHaveLength(1);
  });

  it("leaves the status filter off for the All tab", async () => {
    const calls = stubGateway(() => pageOf([]));
    await exported.fetchPage({ page: 1, size: 200, filters: { status: "all" }, signal: new AbortController().signal });
    expect(calls[0]?.url.searchParams.has("status")).toBe(false);
  });

  it("writes the masked email and phone in read-only columns, and leaves the importable ones blank", () => {
    const row = exported.toRow(LEAD, LOOKUPS);
    expect(row).toEqual({
      fullName: "Meera Iyer",
      company: "Halcyon Textiles",
      email: null,
      phone: null,
      owner: "Grace@Example.com",
      notes: "Asked for a demo.",
      status: "Working",
      source: "Import",
      emailMasked: "m•••@h•••.in",
      phoneMasked: "+•• ••••• ••345",
      id: "lead-1",
      createdAt: "2026-09-20T09:00:00Z",
    });
    expect(Object.keys(row).sort()).toEqual(leadImport.columns.map((c) => c.key).sort());
    const readOnly = leadImport.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    expect(readOnly).toEqual(expect.arrayContaining(["emailMasked", "phoneMasked"]));
  });

  it("writes an unassigned lead's owner as blank", () => {
    expect(exported.toRow({ ...LEAD, owner_id: null }, LOOKUPS).owner).toBeNull();
  });
});
