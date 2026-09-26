import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { LookupSet, ParsedValue } from "@opsgrids/client";

import type { Contact } from "@/api/types";
import { getMySubjectRef } from "@/lib/subject";
import { contactImport } from "./contacts";
import {
  OWNERS_WIRE,
  companyWire,
  contactWire,
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
  ownersAll: makeLookup([
    { label: "sofia@example.com", value: "own-1" },
    { label: "Grace@Example.com", value: "own-2" },
    { label: "former@example.com", value: "own-3" },
  ]),
  companies: makeLookup([
    { label: "Northwind Logistics", value: "comp-1" },
    { label: "Cobalt Financial", value: "comp-2" },
  ]),
  // Two people are on file: one with capitals in the address, one all lower case.
  contactEmails: makeLookup([
    { label: "Priya.Nair@northwind.co", value: "Priya.Nair@northwind.co" },
    { label: "daniel.okafor@cobaltfin.com", value: "daniel.okafor@cobaltfin.com" },
  ]),
};

const ctx = contextWith(LOOKUPS);
const loadLookups = present(contactImport.loadLookups, "loadLookups");
const exported = present(contactImport.export, "export");

beforeEach(() => {
  vi.mocked(getMySubjectRef).mockReturnValue("own-1");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const MINIMAL = { fullName: "Asha Rao", email: "asha.rao@northwind.co", company: "northwind  logistics" };

function payloadFor(cells: Record<string, ParsedValue>) {
  return contactImport.toPayload(parsedRow(contactImport.columns, cells), ctx);
}

describe("contacts: toPayload", () => {
  it("builds the create body from a full row, resolving the company and owner by name", () => {
    expect(
      payloadFor({
        ...MINIMAL,
        owner: "grace@example.com",
        title: "VP Operations",
        status: "customer",
        phone: "+919876543210",
      }),
    ).toEqual({
      full_name: "Asha Rao",
      email: "asha.rao@northwind.co",
      company_id: "comp-1",
      owner_id: "own-2",
      title: "VP Operations",
      status: "customer",
      raw_phone: "+919876543210",
    });
  });

  it("defaults what the New contact form defaults, and sends no phone when there is none", () => {
    const payload = payloadFor(MINIMAL);
    expect(payload).toEqual({
      full_name: "Asha Rao",
      email: "asha.rao@northwind.co",
      company_id: "comp-1",
      owner_id: "own-1",
      title: "",
      status: "lead",
    });
    expect("raw_phone" in payload).toBe(false);
  });

  it("gives a blank owner to the signed-in rep, and refuses when that rep is not an active one", () => {
    vi.mocked(getMySubjectRef).mockReturnValue("own-2");
    expect(payloadFor(MINIMAL).owner_id).toBe("own-2");
    vi.mocked(getMySubjectRef).mockReturnValue("own-3");
    expect(() => payloadFor(MINIMAL)).toThrow(RowProblem);
  });
});

describe("contacts: what the user is told about a very large book", () => {
  it("says an organisation with more than 20,000 contacts cannot import contacts in this release", () => {
    expect(contactImport.notes?.join(" ")).toContain(
      "An organisation with more than 20,000 contacts cannot import contacts in this release",
    );
  });
});

/**
 * The server dedupes contacts on the exact email string, so two spellings that differ only in
 * capitals are two contacts to it. The address reaches `toPayload` as the engine coerced it.
 */
describe("contacts: an email that differs from one on file only in capitals", () => {
  it("still goes to the server when it matches an address on file exactly, so the 409 is reported as skipped", () => {
    expect(payloadFor({ ...MINIMAL, email: "daniel.okafor@cobaltfin.com" }).email).toBe(
      "daniel.okafor@cobaltfin.com",
    );
    expect(payloadFor({ ...MINIMAL, email: "Priya.Nair@northwind.co" }).email).toBe("Priya.Nair@northwind.co");
  });

  it("is refused, naming the address on file, so the same person is not added twice", () => {
    const refused = () => payloadFor({ ...MINIMAL, email: "priya.nair@northwind.co" });
    expect(refused).toThrow(RowProblem);
    expect(refused).toThrow(
      "A contact with this email is already on file as Priya.Nair@northwind.co; addresses that differ only in capitals are treated as the same person.",
    );
    expect(() => payloadFor({ ...MINIMAL, email: "DANIEL.OKAFOR@cobaltfin.com" })).toThrow(
      /already on file as daniel\.okafor@cobaltfin\.com/,
    );
  });

  it("sends an address nobody has, in any capitals", () => {
    expect(payloadFor({ ...MINIMAL, email: "New.Person@northwind.co" }).email).toBe("New.Person@northwind.co");
  });

  it("copes with two addresses on file that differ only in capitals: the exact one is sent, any other spelling is refused", () => {
    const both = contextWith({
      ...LOOKUPS,
      contactEmails: makeLookup([
        { label: "Meera@northwind.co", value: "Meera@northwind.co" },
        { label: "meera@northwind.co", value: "meera@northwind.co" },
      ]),
    });
    const send = (email: string) =>
      contactImport.toPayload(parsedRow(contactImport.columns, { ...MINIMAL, email }), both);
    expect(send("meera@northwind.co").email).toBe("meera@northwind.co");
    expect(send("Meera@northwind.co").email).toBe("Meera@northwind.co");
    expect(() => send("MEERA@northwind.co")).toThrow(RowProblem);
    expect(() => send("MEERA@northwind.co")).toThrow(/already on file in other capitals/);
  });
});

describe("contacts: loadLookups", () => {
  it("reads every page of companies, so a company past the first 200 still resolves", async () => {
    const firstPage = Array.from({ length: 200 }, (_, i) => companyWire(`comp-${i + 1}`, `Company ${i + 1}`));
    const calls = stubGateway(({ url }) => {
      if (url.pathname.endsWith("/customers/owners")) return OWNERS_WIRE;
      if (url.pathname === "/api/v1/contacts") return pageOf([], 1, 200);
      const page = Number(url.searchParams.get("page"));
      // Offset paging over tied names can hand a row out twice; page 2 repeats company 1.
      return page === 1
        ? pageOf(firstPage, 1, 200, 201)
        : pageOf([companyWire("comp-1", "Company 1"), companyWire("comp-201", "Zenith Textiles")], 2, 200, 201);
    });

    const lookups = await loadLookups(new AbortController().signal);

    const companyPages = calls
      .filter((c) => c.url.pathname === "/api/v1/customers")
      .map((c) => [c.url.searchParams.get("page"), c.url.searchParams.get("size")]);
    expect(companyPages).toEqual([
      ["1", "200"],
      ["2", "200"],
    ]);
    expect(lookups.companies?.resolve("zenith textiles")).toBe("comp-201");
    expect(lookups.companies?.resolve("Company 1")).toBe("comp-1");
    expect(lookups.companies?.isAmbiguous("Company 1")).toBe(false);
    expect(lookups.owners?.resolve("sofia@example.com")).toBe("own-1");
  });

  it("reads every contact address already on file, exactly as stored, without offering them in a column", async () => {
    stubGateway(({ url }) => {
      if (url.pathname.endsWith("/customers/owners")) return OWNERS_WIRE;
      if (url.pathname === "/api/v1/contacts") {
        return pageOf([
          contactWire("cont-1", "Priya.Nair@northwind.co", "comp-1"),
          contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2"),
        ]);
      }
      return pageOf([companyWire("comp-1", "Northwind Logistics")]);
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.contactEmails?.labelOf("Priya.Nair@northwind.co")).toBe("Priya.Nair@northwind.co");
    expect(lookups.contactEmails?.labelOf("priya.nair@northwind.co")).toBeUndefined();
    expect(lookups.contactEmails?.resolve("priya.nair@northwind.co")).toBe("Priya.Nair@northwind.co");
    const named = new Set(contactImport.columns.flatMap((c) => (c.lookup === undefined ? [] : [c.lookup])));
    expect(named.has("contactEmails")).toBe(false);
  });
});

describe("contacts: create", () => {
  it("POSTs the payload to /contacts, with raw_phone and no phone field, and returns the new id", async () => {
    const calls = stubGateway(() => contactWire("cont-9", "asha.rao@northwind.co", "comp-1"), 201);
    const payload = payloadFor({ ...MINIMAL, phone: "+919876543210" });
    const created = await contactImport.create(payload, ctx);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url.pathname).toBe("/api/v1/contacts");
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toEqual(payload);
    expect(calls[0]?.body).not.toHaveProperty("phone");
    expect(created).toMatchObject({ id: "cont-9" });
  });
});

describe("contacts: export", () => {
  const CONTACT: Contact = {
    id: "cont-1",
    full_name: "Asha Rao",
    title: "VP Operations",
    email: "asha.rao@northwind.co",
    phone: "+•• •••• ••210",
    company_id: "comp-1",
    status: "qualified",
    owner_id: "own-2",
    last_activity_at: "2026-09-01T09:00:00Z",
    created_at: "2026-01-06T09:00:00Z",
  };

  it("pages the list with the screen's current search and status, and reveals nothing", async () => {
    const calls = stubGateway(() => pageOf([contactWire("cont-1", "asha.rao@northwind.co", "comp-1")], 1, 200));
    const controller = new AbortController();
    const page = await exported.fetchPage({
      page: 1,
      size: 200,
      filters: { search: "asha", status: "qualified" },
      signal: controller.signal,
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.method).toBe("GET");
    expect(calls[0]?.url.pathname).toBe("/api/v1/contacts");
    expect(calls[0]?.url.searchParams.get("q")).toBe("asha");
    expect(calls[0]?.url.searchParams.get("status")).toBe("qualified");
    expect(calls[0]?.url.searchParams.get("size")).toBe("200");
    expect(calls[0]?.signal).toBe(controller.signal);
    expect(page.items.map((c) => c.email)).toEqual(["asha.rao@northwind.co"]);
  });

  it("names a deactivated rep as the owner, so the export says who owned the contact", () => {
    expect(exported.toRow({ ...CONTACT, owner_id: "own-3" }, LOOKUPS).owner).toBe("former@example.com");
  });

  it("writes the phone as the API masked it, in a read-only column, and leaves the importable Phone blank", () => {
    const row = exported.toRow(CONTACT, LOOKUPS);
    expect(row).toEqual({
      fullName: "Asha Rao",
      email: "asha.rao@northwind.co",
      company: "Northwind Logistics",
      owner: "Grace@Example.com",
      title: "VP Operations",
      status: "Qualified",
      phone: null,
      phoneMasked: "+•• •••• ••210",
      lastActivity: "2026-09-01T09:00:00Z",
      id: "cont-1",
      createdAt: "2026-01-06T09:00:00Z",
    });
    expect(Object.keys(row).sort()).toEqual(contactImport.columns.map((c) => c.key).sort());
    const phoneColumn = contactImport.columns.find((c) => c.key === "phoneMasked");
    expect(phoneColumn?.readOnly).toBe(true);
  });
});
