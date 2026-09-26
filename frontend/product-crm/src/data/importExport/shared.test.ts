/**
 * What the four entities share: which owner a blank Owner cell means, the reference lookups the
 * loaders build from the API's full lists, and writing an id back as the label a person knows.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";

import { getMySubjectRef } from "@/lib/subject";
import { OWNERS_WIRE, companyWire, contactWire, contextWith, pageOf, parsedRow, stubGateway } from "./entityTestKit";
import {
  labelFor,
  loadCompanyLookup,
  loadContactLookups,
  loadExistingContactEmails,
  loadOwnerLookups,
  namedOwnerId,
  ownerIdOrSelf,
} from "./shared";

vi.mock("@/lib/subject", () => ({ getMySubjectRef: vi.fn() }));

const signal = new AbortController().signal;

beforeEach(() => {
  vi.mocked(getMySubjectRef).mockReturnValue("own-1");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const OWNER_COLUMNS = [{ key: "owner", header: "Owner email", kind: "text" as const, help: "An owner." }];

function rowWithOwner(owner: string | null) {
  return parsedRow(OWNER_COLUMNS, { owner });
}

const ctx = contextWith({
  owners: makeLookup([
    { label: "sofia@example.com", value: "own-1" },
    { label: "Grace@Example.com", value: "own-2" },
  ]),
});

const NO_ACTIVE_REP = "Your account has no active sales-rep record, so name an owner in the Owner email column.";

describe("ownerIdOrSelf", () => {
  it("is the rep the cell names, whoever is signed in", () => {
    expect(ownerIdOrSelf(rowWithOwner("grace@example.com"), ctx)).toBe("own-2");
  });

  it("is the signed-in rep's own record when the cell is blank and that rep is active", () => {
    expect(ownerIdOrSelf(rowWithOwner(null), ctx)).toBe("own-1");
  });

  it("refuses a blank cell when the account has no rep record at all", () => {
    vi.mocked(getMySubjectRef).mockReturnValue(null);
    expect(() => ownerIdOrSelf(rowWithOwner(null), ctx)).toThrow(RowProblem);
    expect(() => ownerIdOrSelf(rowWithOwner(null), ctx)).toThrow(NO_ACTIVE_REP);
  });

  it("refuses a blank cell when the signed-in rep is deactivated or unknown, which the server would refuse too", () => {
    vi.mocked(getMySubjectRef).mockReturnValue("own-3");
    expect(() => ownerIdOrSelf(rowWithOwner(null), ctx)).toThrow(NO_ACTIVE_REP);
    vi.mocked(getMySubjectRef).mockReturnValue("own-elsewhere");
    expect(() => ownerIdOrSelf(rowWithOwner(null), ctx)).toThrow(NO_ACTIVE_REP);
  });

  it("does not fall back to the caller for a named owner the lookup lacks: that row fails, whatever the engine let through", () => {
    // The engine refuses an unknown or ambiguous owner name before `toPayload` runs, with its own
    // sentence and the column's hint. Reaching this is a bug, and must never quietly mean "me".
    const row = rowWithOwner("nobody@example.com");
    expect(() => ownerIdOrSelf(row, ctx)).toThrow(/engine should have refused/);
    expect(() => ownerIdOrSelf(row, ctx)).not.toThrow(RowProblem);
    expect(() => namedOwnerId(row, ctx)).toThrow(/engine should have refused/);
  });
});

describe("loadOwnerLookups", () => {
  it("offers active reps to the column and keeps every rep for the export", async () => {
    stubGateway(() => OWNERS_WIRE);
    const lookups = await loadOwnerLookups(signal);
    expect(Object.keys(lookups).sort()).toEqual(["owners", "ownersAll"]);
    expect(lookups.owners?.labels).toEqual(["sofia@example.com", "Grace@Example.com"]);
    expect(lookups.ownersAll?.labelOf("own-3")).toBe("former@example.com");
    expect(lookups.owners?.labelOf("own-3")).toBeUndefined();
  });
});

describe("the company and contact lookups", () => {
  const COMPANIES = [companyWire("comp-1", "Northwind Logistics"), companyWire("comp-2", "Cobalt Financial")];
  const CONTACTS = [
    contactWire("cont-1", "Asha.Rao@northwind.co", "comp-1"),
    contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2"),
  ];

  it("map company names to ids, contact emails to ids, and the same emails to their companies", async () => {
    stubGateway(({ url }) => {
      const served: readonly unknown[] = url.pathname.endsWith("/customers") ? COMPANIES : CONTACTS;
      return pageOf(served, 1, 200);
    });
    const companies = await loadCompanyLookup(signal);
    const contacts = await loadContactLookups(signal);
    expect(companies.resolve("cobalt  financial")).toBe("comp-2");
    expect(contacts.contacts?.resolve("asha.rao@NORTHWIND.co")).toBe("cont-1");
    expect(contacts.contactCompanies?.resolve("asha.rao@northwind.co")).toBe("comp-1");
  });

  it("hold each existing contact email as itself, so an exact spelling can be told from another capitalisation", async () => {
    stubGateway(() => pageOf(CONTACTS, 1, 200));
    const emails = await loadExistingContactEmails(signal);
    expect(emails.labelOf("Asha.Rao@northwind.co")).toBe("Asha.Rao@northwind.co");
    expect(emails.labelOf("asha.rao@northwind.co")).toBeUndefined();
    expect(emails.resolve("asha.rao@northwind.co")).toBe("Asha.Rao@northwind.co");
  });

  it("read up to 100 pages of contacts for the addresses on file, twice what the other lookups read", async () => {
    // 52 pages of 200: more than the 50 a lookup reads by default, well inside the 100 this one may.
    const pages = 52;
    stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      const first = (page - 1) * 200;
      const served = Array.from({ length: 200 }, (_, i) =>
        contactWire(`cont-${first + i}`, `person${first + i}@northwind.co`, "comp-1"),
      );
      return pageOf(served, page, 200, pages * 200);
    });
    const emails = await loadExistingContactEmails(signal);
    expect(emails.labelOf("person0@northwind.co")).toBe("person0@northwind.co");
    expect(emails.labelOf(`person${pages * 200 - 1}@northwind.co`)).toBe(`person${pages * 200 - 1}@northwind.co`);
  });

  it("keep a row the list serves on two pages once, so its name is not ambiguous", async () => {
    stubGateway(({ url }) =>
      Number(url.searchParams.get("page")) === 1
        ? pageOf([COMPANIES[0], COMPANIES[1]], 1, 2, 3)
        : pageOf([COMPANIES[0], companyWire("comp-3", "Zenith Textiles")], 2, 2, 3),
    );
    const companies = await loadCompanyLookup(signal);
    expect(companies.resolve("Northwind Logistics")).toBe("comp-1");
    expect(companies.isAmbiguous("Northwind Logistics")).toBe(false);
    expect(companies.resolve("Zenith Textiles")).toBe("comp-3");
  });

  it("refuse a list that stays short of the server's total, rather than look names up in part of it", async () => {
    // The server says three companies exist and only ever serves two of them.
    stubGateway(() => pageOf(COMPANIES, 1, 200, 3));
    await expect(loadCompanyLookup(signal)).rejects.toThrow();
    await expect(loadExistingContactEmails(signal)).rejects.toThrow();
    await expect(loadContactLookups(signal)).rejects.toThrow();
  });
});

describe("labelFor", () => {
  const lookup = makeLookup([{ label: "Northwind Logistics", value: "comp-1" }]);

  it("writes an id as the label the lookup holds for it", () => {
    expect(labelFor(lookup, "comp-1")).toBe("Northwind Logistics");
  });

  it("writes blank for a blank id, an id the lookup does not know, and a lookup that did not load", () => {
    expect(labelFor(lookup, null)).toBeNull();
    expect(labelFor(lookup, "comp-9")).toBeNull();
    expect(labelFor(undefined, "comp-1")).toBeNull();
  });
});
