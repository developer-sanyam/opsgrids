/**
 * The Finance customers entity, checked by the engine's own definition lint and through the
 * functions the engine calls: the sample rows pass validation and become payloads, a row's state
 * may be a name or a GST code and must agree with its GSTIN, an unregistered name already on file
 * is refused, and an export never writes a phone number in full.
 */
import { checkImportEntity, makeLookup, RowSkipped } from "@opsgrids/client";
import type { LookupSet, ParsedRow } from "@opsgrids/client";
import { describe, expect, it } from "vitest";

import type { CustomerSummary } from "@/api/types";

import { customerImport } from "./customerImport";

const NONE_ON_FILE: LookupSet = {
  "finance.customers.unregisteredNames": makeLookup([]),
  "finance.customers.gstins": makeLookup([]),
};
const ctx = (lookups: LookupSet = NONE_ON_FILE) => ({ lookups, signal: new AbortController().signal });

function row(values: Record<string, string | null>): ParsedRow {
  return values;
}

describe("the finance customers entity definition", () => {
  it("passes the engine's definition check", () => {
    expect(checkImportEntity(customerImport, NONE_ON_FILE)).toEqual([]);
  });

  it("refuses rows that came out of an export", () => {
    expect(customerImport.recordIdKey).toBe("id");
  });
});

describe("toPayload", () => {
  it("builds the create body, taking the state from the GSTIN when State is blank", () => {
    const payload = customerImport.toPayload(
      row({ name: "Konkan Traders", gstin: "27aaeco9012h1zg", phone: "9876543210", state: null, city: "Pune", pincode: "411004" }),
      ctx(),
    );
    expect(payload).toMatchObject({
      name: "Konkan Traders",
      gstin: "27AAECO9012H1ZG",
      phone: "9876543210",
      state_code: "27",
      state: "Maharashtra",
      city: "Pune",
      pincode: "411004",
    });
  });

  it("sends an unregistered buyer with no GSTIN and the state the row names", () => {
    const payload = customerImport.toPayload(row({ name: "Ravi Kumar", gstin: null, state: "29" }), ctx());
    expect(payload).toMatchObject({ gstin: null, state_code: "29", state: "Karnataka" });
    expect(payload).not.toHaveProperty("phone");
  });

  it("refuses an unregistered name that is already on file, saying why", () => {
    const lookups: LookupSet = {
      "finance.customers.unregisteredNames": makeLookup([{ label: "Ravi Kumar", value: "c-1" }]),
    };
    expect(() => customerImport.toPayload(row({ name: "ravi kumar", gstin: null }), ctx(lookups))).toThrow(RowSkipped);
    // A GSTIN is a key of its own, checked against the GSTINs on file.
    expect(() =>
      customerImport.toPayload(row({ name: "Ravi Kumar", gstin: "27AAECO9012H1ZG" }), ctx(lookups)),
    ).not.toThrow();
  });

  it("skips a row whose GSTIN is already on file, in any capitals", () => {
    const lookups: LookupSet = {
      ...NONE_ON_FILE,
      "finance.customers.gstins": makeLookup([{ label: "27AAECO9012H1ZG", value: "c-2" }]),
    };
    expect(() => customerImport.toPayload(row({ name: "Konkan", gstin: "27aaeco9012h1zg" }), ctx(lookups))).toThrow(
      RowSkipped,
    );
  });
});

describe("validateRow", () => {
  it("refuses a State that is not the GSTIN's", () => {
    const problem = customerImport.validateRow?.(row({ name: "X", gstin: "27AAECO9012H1ZG", state: "29" }), ctx());
    expect(problem).toMatch(/registered in Maharashtra, but State says Karnataka/);
  });

  it("accepts a matching or blank State", () => {
    expect(customerImport.validateRow?.(row({ name: "X", gstin: "27AAECO9012H1ZG", state: "27" }), ctx())).toBeNull();
    expect(customerImport.validateRow?.(row({ name: "X", gstin: null, state: "29" }), ctx())).toBeNull();
  });
});

describe("export", () => {
  it("writes the masked phone read-only and never the number", () => {
    const summary: CustomerSummary = {
      customer: {
        id: "c-1",
        name: "Konkan Traders",
        contact_name: "Asha",
        email: "a@k.in",
        gstin: "27AAECO9012H1ZG",
        city: "Pune",
        state: "Maharashtra",
        state_code: "27",
        address_line1: "12 FC Road",
        address_line2: "",
        pincode: "411004",
        phone: "•••••• 3210",
        is_tds_deductor: false,
        deducts_tds_194q: false,
        tcs_applicable: false,
      },
      receivables_minor: 0,
      overdue_minor: 0,
      invoice_count: 0,
      last_invoice_date: null,
      standing: "clear",
    };
    const out = customerImport.export?.toRow(summary, NONE_ON_FILE);
    expect(out).toMatchObject({ phone: null, phoneMasked: "•••••• 3210", state: "Maharashtra", id: "c-1" });
  });
});
