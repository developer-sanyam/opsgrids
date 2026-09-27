/**
 * The engine's own lint for an entity definition (`checkImportEntity`), run on all four CRM
 * entities. It catches what would ship to every customer through the sample sheet: a lookup no
 * loader provides, a column without help text, a sample row that fails its own validation.
 *
 * The lookups are what each entity's own `loadLookups` builds from the API's list endpoints for a
 * small organisation, so the check sees the same lookup names and shapes the dialog would.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { checkImportEntity } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { getMySubjectRef } from "@/lib/subject";
import { companyImport } from "./companies";
import { contactImport } from "./contacts";
import { dealImport } from "./deals";
import { OWNERS_WIRE, companyWire, contactWire, pageOf, present, stubGateway } from "./entityTestKit";
import { leadImport } from "./leads";

vi.mock("@/lib/subject", () => ({ getMySubjectRef: vi.fn() }));

const ENTITIES = [
  { name: "companies", entity: companyImport },
  { name: "contacts", entity: contactImport },
  { name: "leads", entity: leadImport },
  { name: "deals", entity: dealImport },
] as const;

interface Organisation {
  companies: ReturnType<typeof companyWire>[];
  contacts: ReturnType<typeof contactWire>[];
}

/** The organisation the sample rows were written for. */
const SAMPLE_ORGANISATION: Organisation = {
  companies: [companyWire("comp-1", "Northwind Logistics"), companyWire("comp-2", "Cobalt Financial")],
  contacts: [
    contactWire("cont-1", "asha.rao@northwind.co", "comp-1"),
    contactWire("cont-2", "daniel.okafor@cobaltfin.com", "comp-2"),
  ],
};

/** One whose first contact does not work at its first company, and whose names are none of the
 *  ones the sample rows use: the sample sheet swaps each example for the organisation's first. */
const OTHER_ORGANISATION: Organisation = {
  companies: [companyWire("comp-7", "Acme Traders"), companyWire("comp-8", "Bluebird Foods")],
  contacts: [
    contactWire("cont-8", "priya@bluebird.example", "comp-8"),
    contactWire("cont-7", "vikram@acme.example", "comp-7"),
  ],
};

async function lookupsOf(
  entity: (typeof ENTITIES)[number]["entity"],
  organisation: Organisation,
): Promise<LookupSet> {
  stubGateway(({ url }) => {
    if (url.pathname.endsWith("/customers/owners")) return OWNERS_WIRE;
    if (url.pathname.endsWith("/customers")) return pageOf(organisation.companies);
    if (url.pathname.endsWith("/contacts")) return pageOf(organisation.contacts);
    throw new Error(`unexpected request ${url.pathname}`);
  });
  return present(entity.loadLookups, "loadLookups")(new AbortController().signal);
}

beforeEach(() => {
  vi.mocked(getMySubjectRef).mockReturnValue("own-1");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each(ENTITIES)("$name definition", ({ entity }) => {
  it("passes the engine's definition check for an organisation that holds the sample names", async () => {
    expect(checkImportEntity(entity, await lookupsOf(entity, SAMPLE_ORGANISATION))).toEqual([]);
  });

  it("passes it for an organisation whose companies and contacts are named otherwise", async () => {
    expect(checkImportEntity(entity, await lookupsOf(entity, OTHER_ORGANISATION))).toEqual([]);
  });
});

/**
 * An update spec accounts for every importable column: it changes it (a field), hands it to an
 * extra step (a contact's phone, a deal's stage), or leaves it out on purpose (a lead's email). A
 * column that is none of these would take whatever a user typed in it and do nothing, and say
 * nothing. The engine's own check enforces the same; this states it for the four CRM lists.
 */
describe.each(ENTITIES)("$name update spec", ({ entity }) => {
  it("accounts for every importable column, so no cell a user fills in is silently ignored", () => {
    const update = present(entity.update, "update");
    const accounted = new Set<string>([
      ...update.fields.map((field) => field.column),
      ...(update.extraColumns ?? []),
      ...(update.notUpdatable ?? []),
    ]);
    const missing = entity.columns
      .filter((column) => column.readOnly !== true && column.importOnly !== true)
      .filter((column) => !accounted.has(column.key))
      .map((column) => column.header);
    expect(missing).toEqual([]);
  });
});

describe("the definition check itself", () => {
  it("reports a lookup that loadLookups does not provide, so a pass above means something", () => {
    const problems = checkImportEntity(companyImport, {});
    expect(problems.join("\n")).toMatch(/lookup "owners", which loadLookups does not provide/);
  });
});
