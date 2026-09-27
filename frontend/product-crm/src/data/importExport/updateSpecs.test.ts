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
    })),
    keyColumn: spec.keyColumn,
    hasKeyOf: spec.keyOf !== undefined,
    hasCurrency: spec.currencyOf !== undefined,
    hasExtras: spec.extras !== undefined,
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
    beyondFields: [],
    key: "name",
  },
  {
    name: "contacts",
    shape: shapeOf(contactImport),
    // ContactPatch; the phone has its own route
    accepted: ["full_name", "title", "email", "company_id", "owner_id", "status"],
    clear: { title: "" },
    immutable: [],
    beyondFields: ["phone"],
    key: "email",
  },
  {
    name: "leads",
    shape: shapeOf(leadImport),
    // LeadPatch, without `status`, which is a read-only column
    accepted: ["full_name", "company", "owner_id", "notes"],
    clear: { company: null, notes: "" },
    immutable: ["email", "phone"],
    beyondFields: [],
    key: undefined,
  },
  {
    name: "deals",
    shape: shapeOf(dealImport),
    // DealPatch, plus `stage`, which `send` takes to the stage endpoint
    accepted: ["name", "company_id", "primary_contact_id", "owner_id", "value_minor", "currency", "close_date", "probability", "source", "stage"],
    clear: { contact: null, source: "" },
    immutable: [],
    beyondFields: [],
    key: "name",
  },
];

describe.each(ENTITIES)("$name: the update spec", ({ shape, accepted, clear, immutable, beyondFields, key }) => {
  it("covers every importable column, so no cell a user fills in is silently ignored", () => {
    const covered = [...shape.fields.map((f) => f.column), ...beyondFields];
    expect(covered.sort()).toEqual([...shape.importable].sort());
  });

  it("names only importable columns for its fields and its key", () => {
    for (const field of shape.fields) expect(shape.importable).toContain(field.column);
    if (shape.keyColumn !== undefined) expect(shape.importable).toContain(shape.keyColumn);
  });

  it("finds a row's record by the list's key, or by its ID alone where the list has none", () => {
    expect(shape.keyColumn).toBe(key);
    expect(shape.hasKeyOf).toBe(key !== undefined);
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

  it("uses extra steps for the contact's phone only: the deal's stage travels with its own send", () => {
    expect(ENTITIES.map((e) => [e.name, e.shape.hasExtras])).toEqual([
      ["companies", false],
      ["contacts", true],
      ["leads", false],
      ["deals", false],
    ]);
  });
});
