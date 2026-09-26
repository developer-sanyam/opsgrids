/**
 * What every CRM entity definition holds to that `checkImportEntity` (see `checkEntities.test.ts`)
 * does not say: the names the dialog and the downloaded files use, a handful of examples, the
 * rules a user is told, that the list can be exported, which column carries the record's id, what
 * a user is told when a name is not in a list, and which text may run over several lines.
 */
import { describe, expect, it } from "vitest";

import { companyImport } from "./companies";
import { contactImport } from "./contacts";
import { dealImport } from "./deals";
import { leadImport } from "./leads";

const OWNER_HINT = "Use the email of an active sales rep, see the Lists sheet. Leave blank to assign it to yourself.";
const OWNER_HINT_AUTO = "Use the email of an active sales rep, see the Lists sheet. Leave blank to assign automatically.";

const ENTITIES = [
  { name: "companies", entity: companyImport, id: "crm.companies", noun: "companies", owner: OWNER_HINT, multiline: [] },
  { name: "contacts", entity: contactImport, id: "crm.contacts", noun: "contacts", owner: OWNER_HINT, multiline: [] },
  { name: "leads", entity: leadImport, id: "crm.leads", noun: "leads", owner: OWNER_HINT_AUTO, multiline: ["notes"] },
  { name: "deals", entity: dealImport, id: "crm.deals", noun: "deals", owner: OWNER_HINT, multiline: [] },
] as const;

/** What the engine adds to its "is not in the list" error, by the lookup the column names. */
const HINTS: Record<string, string> = {
  companies: "The company must already exist. Import companies first, then contacts and deals.",
  contacts: "The contact must already exist and work at that company.",
};

describe.each(ENTITIES)("$name entity definition", ({ entity, id, noun, owner, multiline }) => {
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

  it("names its read-only id column as the record id, so a row that came from an export is refused", () => {
    expect(entity.recordIdKey).toBe("id");
    const column = entity.columns.find((c) => c.key === entity.recordIdKey);
    expect(column?.readOnly).toBe(true);
    expect(column?.help).toMatch(/refused on import/);
    expect(entity.notes?.join(" ")).toMatch(/cannot be imported again as it is/);
  });

  it("tells the user how to fix a name that is not in a list, on every column that names a list", () => {
    const lookups = entity.columns.filter((c) => c.lookup !== undefined);
    expect(lookups.length).toBeGreaterThan(0);
    for (const column of lookups) {
      const expected = column.lookup === "owners" ? owner : HINTS[column.lookup ?? ""];
      expect(expected, `a hint is expected for ${column.header}`).toBeDefined();
      expect(column.lookupHint, column.header).toBe(expected);
    }
  });

  it("lets only the columns that hold free text over several lines take a line break", () => {
    expect(entity.columns.filter((c) => c.multiline === true).map((c) => c.key)).toEqual(multiline);
  });
});
