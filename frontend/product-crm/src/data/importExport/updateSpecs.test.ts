/**
 * What every CRM `update` spec holds to, stated once across the four lists so a change to one list
 * cannot quietly drop a column, widen what may be emptied, or send a key the endpoint refuses. The
 * expectations are the update survey's findings for these endpoints (`extra="forbid"` on every
 * Patch schema; `null` answered with a 422, a 409 or a 500 except on the few nullable fields).
 */
import { describe, expect, it } from "vitest";

import type { ImportEntity } from "@opsgrids/client";

import { companyImport } from "./companies";
import { contactImport } from "./contacts";
import { dealImport } from "./deals";
import { leadImport } from "./leads";
import { updateSpecOf } from "./updateTestKit";

/** An entity's update spec without its item type, so the four lists can share one table. */
function shapeOf<Payload, Item, Filters>(entity: ImportEntity<Payload, Item, Filters>) {
  const spec = updateSpecOf(entity);
  return {
    importable: entity.columns.filter((c) => c.readOnly !== true).map((c) => c.key),
    fields: spec.fields.map((f) => ({
      column: f.column,
      sent: f.field ?? f.column,
      clear: f.clear,
      immutable: f.immutable === true,
      resolves: f.resolve !== undefined,
    })),
    lookupColumns: entity.columns
      .filter((c) => c.lookup !== undefined)
      .map((c) => ({ key: c.key, add: c.lookupHint, update: c.lookupHintUpdate })),
    keyColumn: spec.keyColumn,
    hasKeyOf: spec.keyOf !== undefined,
    keyMatch: spec.keyMatch ?? "exact",
    hasRecordLabel: spec.recordLabel !== undefined,
    hasCurrency: spec.currencyOf !== undefined,
    hasExtras: spec.extras !== undefined,
    extraColumns: spec.extraColumns ?? [],
    notUpdatable: spec.notUpdatable ?? [],
    notes: (entity.notes ?? []).join(" "),
    recordIdHelp: entity.columns.find((c) => c.key === entity.recordIdKey)?.help ?? "",
  };
}

const ENTITIES = [
  {
    name: "companies",
    shape: shapeOf(companyImport),
    // CompanyPatch
    accepted: ["name", "owner_id", "domain", "industry", "size_band", "city", "country", "status", "annual_revenue_minor", "currency"],
    clear: { domain: "", industry: "", sizeBand: "", city: "", country: "" },
    immutable: [],
    extraColumns: [],
    notUpdatable: [],
    key: "name",
    // The server compares company names ignoring capitals (`get_company_by_name`), the one CRM key that does.
    keyMatch: "caseless",
  },
  {
    name: "contacts",
    shape: shapeOf(contactImport),
    // ContactPatch; the phone has its own route
    accepted: ["full_name", "title", "email", "company_id", "owner_id", "status"],
    clear: { title: "" },
    immutable: [],
    extraColumns: ["phone"],
    notUpdatable: [],
    key: "email",
    // The server dedupes emails on the exact string, so a case variant is another contact.
    keyMatch: "exact",
  },
  {
    name: "leads",
    shape: shapeOf(leadImport),
    // LeadPatch, without `status`, which is a read-only column
    accepted: ["full_name", "company", "owner_id", "notes"],
    clear: { company: null, notes: "" },
    // Email and phone are `notUpdatable`, which the engine refuses itself: not update fields at all.
    immutable: [],
    extraColumns: [],
    notUpdatable: ["email", "phone"],
    key: undefined,
    keyMatch: "exact",
  },
  {
    name: "deals",
    shape: shapeOf(dealImport),
    // DealPatch's plain fields. Its `probability` is sent by an extra step (so that a stage move,
    // which resets it, can carry it), and `stage` only moves through `POST /deals/{id}/stage`.
    accepted: ["name", "company_id", "primary_contact_id", "owner_id", "value_minor", "currency", "close_date", "source"],
    clear: { contact: null, source: "" },
    immutable: [],
    extraColumns: ["stage", "probability"],
    notUpdatable: [],
    key: "name",
    // Deal names are unique on the exact string (`get_deal_by_name`): 'Q4 Renewal' and 'Q4 renewal' are two deals.
    keyMatch: "exact",
  },
];

describe.each(ENTITIES)("$name: the update spec", ({ shape, accepted, clear, immutable, extraColumns, notUpdatable, key, keyMatch }) => {
  it("names only importable columns for its fields, its extra steps and its key", () => {
    for (const field of shape.fields) expect(shape.importable).toContain(field.column);
    for (const column of [...shape.extraColumns, ...shape.notUpdatable]) expect(shape.importable).toContain(column);
    if (shape.keyColumn !== undefined) expect(shape.importable).toContain(shape.keyColumn);
  });

  it("declares the columns its extra steps handle and the ones it leaves out on purpose", () => {
    expect(shape.extraColumns).toEqual(extraColumns);
    expect(shape.notUpdatable).toEqual(notUpdatable);
    expect(shape.fields.filter((f) => shape.extraColumns.includes(f.column))).toEqual([]);
  });

  it("finds a row's record by the list's key, or by its ID alone where the list has none", () => {
    expect(shape.keyColumn).toBe(key);
    expect(shape.hasKeyOf).toBe(key !== undefined);
  });

  it("matches that key the way the server's uniqueness rule does: capitals count except for company names", () => {
    expect(shape.keyMatch).toBe(keyMatch);
  });

  it("names the record a row updates, so a shifted Record ID column is seen in the preview", () => {
    expect(shape.hasRecordLabel).toBe(true);
  });

  it("sends only keys the endpoint accepts, and never one it marks immutable", () => {
    const sent = shape.fields.filter((f) => !f.immutable).map((f) => f.sent);
    expect(sent.filter((k) => !accepted.includes(k))).toEqual([]);
    expect(shape.fields.filter((f) => f.immutable).map((f) => f.column)).toEqual(immutable);
  });

  it("can empty exactly the fields the endpoint can empty, each with the value it accepts", () => {
    const clearable = Object.fromEntries(shape.fields.filter((f) => f.clear !== undefined).map((f) => [f.column, f.clear]));
    expect(clearable).toEqual(clear);
  });

  it("judges every lookup column itself, since the engine does not on a row that updates a record", () => {
    for (const { key } of shape.lookupColumns) {
      expect(shape.fields.find((f) => f.column === key)?.resolves, `${key} resolves its label`).toBe(true);
    }
  });

  it("advises a user who names something a list does not have without the add-mode default", () => {
    expect(shape.lookupColumns.length).toBeGreaterThan(0);
    for (const { key, add, update } of shape.lookupColumns) {
      expect(update, `${key} has a hint for updating`).toBeDefined();
      expect(update).not.toBe(add);
      expect(update).toMatch(/Leave blank to keep the current/);
      expect(update).not.toMatch(/assign it to yourself|assign automatically/);
    }
  });

  it("says how a row finds its record and that [clear] empties a field", () => {
    expect(shape.notes).toMatch(/When updating, a row with a .+ ID changes that/);
    expect(shape.notes).toContain("[clear]");
  });

  it("keeps the sentence about an exported file, now scoped to adding only", () => {
    expect(shape.notes).toMatch(/With Add new records only, .*cannot be imported again as it is/);
    expect(shape.recordIdHelp).toMatch(/refused on import/);
    expect(shape.recordIdHelp).toMatch(/update existing, it names the/);
  });
});

describe("what differs between the lists", () => {
  it("names the stored currency where a list has amounts, and nowhere else", () => {
    expect(ENTITIES.map((e) => [e.name, e.shape.hasCurrency])).toEqual([
      ["companies", true],
      ["contacts", false],
      ["leads", false],
      ["deals", true],
    ]);
  });

  it("uses extra steps for the contact's phone and the deal's stage and probability only", () => {
    expect(ENTITIES.map((e) => [e.name, e.shape.hasExtras])).toEqual([
      ["companies", false],
      ["contacts", true],
      ["leads", false],
      ["deals", true],
    ]);
  });
});
