/**
 * What every CRM entity definition holds to that `checkImportEntity` (see `checkEntities.test.ts`)
 * does not say: the names the dialog and the downloaded files use, a handful of examples, the
 * rules a user is told, and that the list can be exported.
 */
import { describe, expect, it } from "vitest";

import { companyImport } from "./companies";
import { contactImport } from "./contacts";
import { dealImport } from "./deals";
import { leadImport } from "./leads";

const ENTITIES = [
  { name: "companies", entity: companyImport, id: "crm.companies", noun: "companies" },
  { name: "contacts", entity: contactImport, id: "crm.contacts", noun: "contacts" },
  { name: "leads", entity: leadImport, id: "crm.leads", noun: "leads" },
  { name: "deals", entity: dealImport, id: "crm.deals", noun: "deals" },
] as const;

describe.each(ENTITIES)("$name entity definition", ({ entity, id, noun }) => {
  it("is named for the dialog and the files", () => {
    expect(entity.id).toBe(id);
    expect(entity.noun).toBe(noun);
    expect(entity.fileStem).toBe(noun);
  });

  it("has two or three sample rows", () => {
    expect(entity.sampleRows.length).toBeGreaterThanOrEqual(2);
    expect(entity.sampleRows.length).toBeLessThanOrEqual(3);
  });

  it("states the rules a user has to know", () => {
    expect(entity.notes?.length).toBeGreaterThan(0);
  });

  it("can be exported", () => {
    expect(entity.export).toBeDefined();
  });
});
