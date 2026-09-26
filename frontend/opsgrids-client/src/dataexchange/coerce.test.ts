import { describe, expect, it } from "vitest";

import { InvalidCell } from "./cells";
import type { CellInput } from "./cells";
import { coerceRow, coerceValue } from "./coerce";
import type { Coerced } from "./coerce";
import { makeLookup } from "./lookups";
import { makeProductEntity, productLookups } from "./testkit";
import { RowProblem } from "./types";
import type { ImportColumn, ImportContext } from "./types";

const env = { lookups: productLookups };
const ctx: ImportContext = { lookups: productLookups, signal: new AbortController().signal };

function column(kind: ImportColumn["kind"], extra: Partial<ImportColumn> = {}): ImportColumn {
  return { key: "field", header: "Field", kind, help: "A field.", ...extra };
}

function value(col: ImportColumn, cell: CellInput | undefined, currency?: string): unknown {
  const result = coerceValue(col, cell, { ...env, currency });
  if (!result.ok) throw new Error(`expected a value, got: ${result.error}`);
  return result.value;
}

function error(col: ImportColumn, cell: CellInput | undefined, currency?: string): string {
  const result: Coerced = coerceValue(col, cell, { ...env, currency });
  if (result.ok) throw new Error(`expected an error, got: ${String(result.value)}`);
  return result.error;
}

describe("blank cells", () => {
  it("are null when optional, for every kind", () => {
    for (const kind of ["text", "integer", "decimal", "money", "date", "boolean", "email", "phone"] as const) {
      expect(value(column(kind), null)).toBeNull();
      expect(value(column(kind), undefined)).toBeNull();
      expect(value(column(kind), "   ")).toBeNull();
    }
    expect(value(column("enum", { options: [{ value: "a", label: "A" }] }), "")).toBeNull();
  });

  it("say which column is required, using its header", () => {
    const required = column("text", { header: "Company name", required: true });
    expect(error(required, null)).toBe("Company name is required");
    expect(error(required, "  ")).toBe("Company name is required");
    expect(error(column("integer", { header: "Quantity", required: true }), undefined)).toBe("Quantity is required");
  });

  it("do not treat zero or false as blank", () => {
    expect(value(column("integer", { required: true }), 0)).toBe(0);
    expect(value(column("boolean", { required: true }), false)).toBe(false);
  });
});

describe("invalid cells", () => {
  it("become a sentence about the column, never a blank", () => {
    const cell = new InvalidCell("#DIV/0!", "contains the error #DIV/0!");
    expect(error(column("integer", { header: "Quantity" }), cell)).toBe("Quantity contains the error #DIV/0!");
    expect(error(column("text", { header: "Note" }), cell)).toBe("Note contains the error #DIV/0!");
  });
});

describe("text", () => {
  const text = column("text", { header: "Name" });

  it("trims and keeps inner spacing as typed", () => {
    expect(value(text, "  Steel   bracket  ")).toBe("Steel   bracket");
  });

  it("rejects control characters, including a line break and a tab, in an ordinary column", () => {
    expect(error(text, "a\u0000b")).toMatch(/Name contains .*control character/);
    expect(error(text, "line one\nline two")).toMatch(/line break/);
    expect(error(text, "a\tb")).toMatch(/control character/);
    expect(error(text, "a\u001bb")).toMatch(/control character/);
    expect(error(text, "a\u007fb")).toMatch(/control character/);
    expect(error(text, "a\u0085b")).toMatch(/control character/);
  });

  it("keeps a line break or tab in a column marked multiline, so a note can be imported", () => {
    const note = column("text", { header: "Notes", multiline: true });
    expect(value(note, "line one\nline two")).toBe("line one\nline two");
    expect(value(note, "a\tb")).toBe("a\tb");
    expect(value(note, "  first\r\nsecond  ")).toBe("first\r\nsecond");
    expect(error(note, "a\u0000b")).toMatch(/Notes contains a control character/);
  });

  it("still refuses a line break in an email", () => {
    const email = column("email", { header: "Email" });
    expect(error(email, "a@b.co\nx")).toBeDefined();
  });

  it("enforces maxLength in characters, not UTF-16 units", () => {
    const short = column("text", { header: "Code", maxLength: 3 });
    expect(value(short, "abc")).toBe("abc");
    expect(error(short, "abcd")).toBe("Code is too long (4 characters; the limit is 3)");
    expect(value(short, "😀😀😀")).toBe("😀😀😀");
  });

  it("enforces the pattern and says what is expected", () => {
    const sku = column("text", { header: "SKU", pattern: /^[A-Z0-9-]+$/, patternHint: "Capital letters, digits, -" });
    expect(value(sku, "AB-12")).toBe("AB-12");
    expect(error(sku, "ab 12")).toBe("SKU is not in the expected format: Capital letters, digits, -");
    expect(error(column("text", { header: "SKU", pattern: /^\d+$/ }), "x")).toBe("SKU is not in the expected format");
  });

  it("is not fooled by a global or sticky pattern remembering its last match", () => {
    const sku = column("text", { pattern: /^A\d$/g });
    expect(value(sku, "A1")).toBe("A1");
    expect(value(sku, "A2")).toBe("A2");
    expect(value(sku, "A3")).toBe("A3");
  });

  it("turns a numeric cell into its integer string, never in exponent form", () => {
    expect(value(text, 123)).toBe("123");
    expect(value(text, 1e21)).toBe("1000000000000000000000");
    expect(value(text, 9007199254740991)).toBe("9007199254740991");
    expect(value(text, 12.5)).toBe("12.5");
  });

  it("turns a boolean into TRUE or FALSE", () => {
    expect(value(text, true)).toBe("TRUE");
    expect(value(text, false)).toBe("FALSE");
  });

  describe("a date cell where text belongs", () => {
    // "1-10" or "11-50" typed into a General cell becomes 10 January or 11 May.
    const turned = "Name was turned into a date by Excel (for example 1-10 becomes 10 January). Format the column as Text and type it again.";

    it("is refused, for the text Excel turned into it, with the way out", () => {
      expect(error(text, new Date(Date.UTC(2026, 0, 10)))).toBe(turned);
      expect(error(text, new Date(Date.UTC(2026, 4, 11)))).toBe(turned);
    });

    it("is refused in every kind of text column: multiline, lookup and enum as well", () => {
      const date = new Date(Date.UTC(2026, 0, 10));
      expect(error(column("text", { header: "Name", multiline: true }), date)).toBe(turned);
      expect(error(column("text", { header: "Name", lookup: "categories" }), date)).toBe(turned);
      const size = column("enum", { header: "Name", options: [{ value: "1-10", label: "1-10" }] });
      expect(error(size, date)).toBe(turned);
    });

    it("is not touched when the text was typed as text", () => {
      expect(value(text, "1-10")).toBe("1-10");
      expect(value(text, "11-50")).toBe("11-50");
      expect(value(column("enum", { options: [{ value: "1-10", label: "1-10" }] }), "1-10")).toBe("1-10");
    });

    it("does not affect a date column", () => {
      expect(value(column("date"), new Date(Date.UTC(2026, 0, 10)))).toBe("2026-01-10");
    });
  });
});

describe("integer", () => {
  const quantity = column("integer", { header: "Quantity", min: 0, max: 100 });

  it("accepts a whole number as a number or as text", () => {
    expect(value(quantity, 5)).toBe(5);
    expect(value(quantity, "5")).toBe(5);
    expect(value(quantity, " 007 ")).toBe(7);
    expect(value(quantity, 3.0000000000000004)).toBe(3);
  });

  it("refuses a fraction, exponent form, separators and words", () => {
    for (const cell of [5.5, "5.5", "1e2", "1,000", "five", "5 units"]) {
      expect(error(quantity, cell), String(cell)).toBe("Quantity must be a whole number");
    }
    expect(error(quantity, true)).toBe("Quantity must be a whole number");
  });

  it("enforces both bounds and names them", () => {
    expect(error(quantity, -1)).toBe("Quantity must be between 0 and 100");
    expect(error(quantity, 101)).toBe("Quantity must be between 0 and 100");
    expect(error(column("integer", { header: "Q", min: 1 }), 0)).toBe("Q must be at least 1");
    expect(error(column("integer", { header: "Q", max: 9 }), 10)).toBe("Q must be at most 9");
  });

  it("refuses a number beyond the safe integers", () => {
    expect(error(column("integer", { header: "Q" }), "99999999999999999999")).toBe("Q is too large");
  });
});

describe("decimal", () => {
  const weight = column("decimal", { header: "Weight", min: 0 });

  it("accepts plain numbers", () => {
    expect(value(weight, 0.1)).toBe(0.1);
    expect(value(weight, "0.1")).toBe(0.1);
    expect(value(weight, ".5")).toBe(0.5);
    expect(value(weight, 12)).toBe(12);
    expect(value(weight, 0.1 + 0.2)).toBe(0.3);
  });

  it("refuses commas, exponent notation and words", () => {
    for (const cell of ["1,5", "1e3", "abc", "1.2.3", true]) {
      expect(error(weight, cell), String(cell)).toMatch(/^Weight must be a plain number/);
    }
  });

  it("enforces its bounds", () => {
    expect(error(weight, -0.1)).toBe("Weight must be at least 0");
    expect(value(column("decimal", { min: -5 }), "-2.5")).toBe(-2.5);
  });
});

describe("money", () => {
  const price = column("money", { header: "Price", min: 0, max: 1000 });

  it("gives integer minor units by the row's currency", () => {
    expect(value(price, "1.50", "INR")).toBe(150);
    expect(value(price, 999, "JPY")).toBe(999);
    expect(value(price, "1.234", "KWD")).toBe(1234);
    expect(value(price, 0.1, "USD")).toBe(10);
    expect(value(price, 0.1, "KWD")).toBe(100);
  });

  it("uses the column's default currency, then INR, when the row names none", () => {
    expect(value(price, "12.34")).toBe(1234);
    expect(value(column("money", { defaultCurrency: "JPY" }), "12")).toBe(12);
  });

  it("refuses more decimals than the currency allows and says which currency", () => {
    expect(error(price, "1.5", "JPY")).toBe("Price must be a whole number for JPY, which has no decimal places");
    expect(error(price, "1.005", "INR")).toBe("Price can have at most 2 decimal places for INR");
    expect(error(price, "1.2345", "KWD")).toBe("Price can have at most 3 decimal places for KWD");
  });

  it("refuses negatives, exponent form, thousands separators and words", () => {
    expect(error(price, "-1", "INR")).toBe("Price cannot be negative");
    expect(error(price, "1e2", "INR")).toMatch(/exponent/);
    expect(error(price, "1,000.00", "INR")).toMatch(/commas/);
    expect(error(price, "free", "INR")).toMatch(/must be a plain number/);
    expect(error(price, true, "INR")).toMatch(/must be a plain number/);
  });

  it("checks bounds in major units, exactly", () => {
    expect(value(price, "1000", "INR")).toBe(100000);
    expect(error(price, "1000.01", "INR")).toBe("Price must be between 0 and 1000");
    expect(value(column("money", { header: "P", min: 0.01 }), "0.01", "INR")).toBe(1);
    expect(error(column("money", { header: "P", min: 0.01 }), "0", "INR")).toBe("P must be at least 0.01");
    expect(error(column("money", { header: "P", max: 5 }), "5.01", "INR")).toBe("P must be at most 5");
  });
});

describe("date", () => {
  const launched = column("date", { header: "Launch date" });

  it("reads a real date cell in UTC, so the calendar day never shifts with the time zone", () => {
    expect(value(launched, new Date(Date.UTC(2026, 2, 4)))).toBe("2026-03-04");
    expect(value(launched, new Date(Date.UTC(2026, 2, 4, 23, 59, 59)))).toBe("2026-03-04");
    expect(value(launched, new Date(Date.UTC(2026, 11, 31, 0, 0, 0)))).toBe("2026-12-31");
  });

  it("accepts an ISO string and returns it unchanged", () => {
    expect(value(launched, "2026-03-04")).toBe("2026-03-04");
    expect(value(launched, " 2028-02-29 ")).toBe("2028-02-29");
  });

  it("refuses an ambiguous string and tells the user to write YYYY-MM-DD", () => {
    for (const cell of ["03/04/2026", "3/4/26", "03-04-2026", "03.04.2026"]) {
      const message = error(launched, cell);
      expect(message, cell).toContain("is ambiguous");
      expect(message, cell).toContain("YYYY-MM-DD");
    }
  });

  it("refuses text, impossible dates and numbers", () => {
    expect(error(launched, "4 March 2026")).toBe("Launch date must be a date written as YYYY-MM-DD, e.g. 2026-03-04");
    expect(error(launched, "2026-02-30")).toMatch(/is not a real date/);
    expect(error(launched, "2027-02-29")).toMatch(/is not a real date/);
    expect(error(launched, 46085)).toMatch(/YYYY-MM-DD/);
    expect(error(launched, true)).toMatch(/YYYY-MM-DD/);
  });

  it("refuses a time-only cell and an invalid date", () => {
    expect(error(launched, new Date(Date.UTC(1899, 11, 30, 12, 30)))).toBe("Launch date is not a valid date");
    expect(error(launched, new Date(Number.NaN))).toBe("Launch date is not a valid date");
  });
});

describe("boolean", () => {
  const active = column("boolean", { header: "Active" });

  it("accepts yes, no, true, false, y, n, 1 and 0 in any case", () => {
    for (const cell of ["yes", "YES", "Y", "y", "true", "True", "1", 1, true, " Yes "]) {
      expect(value(active, cell), String(cell)).toBe(true);
    }
    for (const cell of ["no", "NO", "N", "n", "false", "False", "0", 0, false, " no "]) {
      expect(value(active, cell), String(cell)).toBe(false);
    }
  });

  it("refuses anything else", () => {
    expect(error(active, "maybe")).toBe("Active must be Yes or No");
    expect(error(active, 2)).toBe("Active must be Yes or No");
    expect(error(active, new Date())).toBe("Active must be Yes or No");
  });
});

describe("enum", () => {
  const status = column("enum", {
    header: "Status",
    options: [
      { value: "in_stock", label: "In stock" },
      { value: "discontinued", label: "Discontinued" },
    ],
  });

  it("takes the label or the value, ignoring case and spacing, and returns the value", () => {
    expect(value(status, "In stock")).toBe("in_stock");
    expect(value(status, "in stock")).toBe("in_stock");
    expect(value(status, "IN_STOCK")).toBe("in_stock");
    expect(value(status, "  discontinued ")).toBe("discontinued");
    expect(value(status, "In   Stock")).toBe("in_stock");
  });

  it("lists the allowed values when it refuses one", () => {
    expect(error(status, "Sold out")).toBe(
      "'Sold out' is not one of the allowed values for Status: In stock, Discontinued",
    );
  });

  it("keeps a long list short", () => {
    const many = column("enum", {
      header: "Code",
      options: Array.from({ length: 20 }, (_, i) => ({ value: `v${i}`, label: `Label ${i}` })),
    });
    const message = error(many, "nope");
    expect(message).toContain("Label 11");
    expect(message).not.toContain("Label 12");
    expect(message).toContain("and 8 more (see the Instructions sheet)");
  });

  it("matches a numeric cell against numeric option values", () => {
    const grade = column("enum", { options: [{ value: "1", label: "Gold" }] });
    expect(value(grade, 1)).toBe("1");
  });
});

describe("email", () => {
  const email = column("email", { header: "Email" });
  const invalid = "Email must be a valid email address, e.g. name@example.com";

  it("trims, keeps the local part exactly as typed and lower-cases only the domain", () => {
    // The server dedupes on the exact string, and EmailStr lower-cases only the domain.
    expect(value(email, "  Ada.Lovelace@Example.COM ")).toBe("Ada.Lovelace@example.com");
    expect(value(email, "ORDERS@EXAMPLE.COM")).toBe("ORDERS@example.com");
    expect(value(email, "ada@example.com")).toBe("ada@example.com");
  });

  it("gives an address that came out of an export back unchanged, so a re-import matches the record", () => {
    for (const stored of ["Ada.Lovelace@example.com", "o.Brien+Tag@example.co.uk", "X_Y@sub.example.org"]) {
      expect(value(email, stored)).toBe(stored);
    }
  });

  it("refuses what is not an address", () => {
    for (const cell of ["ada", "ada@", "@example.com", "ada@example", "a b@example.com", "a@@example.com", 42]) {
      expect(error(email, cell), String(cell)).toBe(invalid);
    }
  });

  it("refuses an address longer than 254 characters", () => {
    const long = `${"a".repeat(250)}@example.com`;
    expect(error(email, long)).toMatch(/Email is too long/);
    expect(value(email, `${"a".repeat(64)}@example.com`)).toBe(`${"a".repeat(64)}@example.com`);
  });

  it("refuses a line break inside the address, and forgives one at the edge", () => {
    expect(error(email, "a@example.com\nx")).toBe(invalid);
    expect(value(email, "a@example.com\n")).toBe("a@example.com");
  });
});

describe("phone", () => {
  const phone = column("phone", { header: "Phone" });

  it("strips spaces, dashes, dots and brackets and keeps the plus", () => {
    expect(value(phone, "+91 98765 43210")).toBe("+919876543210");
    expect(value(phone, "+91-98765-43210")).toBe("+919876543210");
    expect(value(phone, "+1 (415) 555.2671")).toBe("+14155552671");
    expect(value(phone, "  +44 20 7946 0958 ")).toBe("+442079460958");
  });

  it("never guesses a country code", () => {
    const hint = "Phone must start with + and the country code, e.g. +919876543210";
    expect(error(phone, "9876543210")).toBe(hint);
    expect(error(phone, "09876543210")).toBe(hint);
    expect(error(phone, "0091 98765 43210")).toBe(hint);
  });

  it("turns a numeric cell into digits and then refuses it for having no plus", () => {
    // Excel drops the + when a number-formatted cell holds "+919876543210".
    expect(error(phone, 919876543210)).toBe("Phone must start with + and the country code, e.g. +919876543210");
  });

  it("enforces E.164 length and a non-zero first digit", () => {
    expect(error(phone, "+1234567")).toMatch(/must start with \+/);
    expect(error(phone, "+1234567890123456")).toMatch(/must start with \+/);
    expect(error(phone, "+0123456789")).toMatch(/must start with \+/);
    expect(value(phone, "+12345678")).toBe("+12345678");
    expect(value(phone, "+123456789012345")).toBe("+123456789012345");
  });
});

describe("lookup", () => {
  const category = column("text", { header: "Category", lookup: "categories" });

  it("accepts a label ignoring case and spacing", () => {
    expect(value(category, "hardware")).toBe("hardware");
    expect(value(category, "  Fasteners ")).toBe("Fasteners");
  });

  it("names the value and the column when it is unknown", () => {
    expect(error(category, "Tools")).toBe(
      "'Tools' is not one of the accepted values for Category (see the Lists sheet).",
    );
  });

  it("says when a label matches more than one entry", () => {
    expect(error(category, "twin")).toContain("matches more than one");
    // An ambiguous label is not on the Lists sheet, so the message must not send the user there.
    expect(error(category, "twin")).not.toContain("Lists sheet");
  });

  it("needs no pluralising: the header is quoted as it is, whatever it ends in", () => {
    const lookups = { units: makeLookup([]), owners: makeLookup([]), addresses: makeLookup([]) };
    const ask = (header: string, name: string): string => {
      const result = coerceValue(column("text", { header, lookup: name }), "kgs", { lookups });
      return result.ok ? "" : result.error;
    };
    expect(ask("Unit of measure", "units")).toBe(
      "'kgs' is not one of the accepted values for Unit of measure (see the Lists sheet).",
    );
    expect(ask("Owner email", "owners")).toContain("accepted values for Owner email (");
    expect(ask("Address", "addresses")).toContain("accepted values for Address (");
    expect(ask("Unit of measure", "units")).not.toMatch(/measures|Measures/);
  });

  it("adds the column's hint to an unknown value, after the reason", () => {
    const manager = column("text", {
      header: "Manager email",
      lookup: "categories",
      lookupHint: "Import the manager first, then run this file again: rows already imported are skipped.",
    });
    expect(error(manager, "nobody@example.com")).toBe(
      "'nobody@example.com' is not one of the accepted values for Manager email (see the Lists sheet). Import the manager first, then run this file again: rows already imported are skipped.",
    );
  });

  it("adds the hint to an ambiguous value too", () => {
    const hinted = column("text", { header: "Category", lookup: "categories", lookupHint: "Rename one of them." });
    const message = error(hinted, "twin");
    expect(message).toContain("matches more than one of the accepted values for Category");
    expect(message.endsWith("so it cannot be chosen. Rename one of them.")).toBe(true);
  });

  it("adds nothing when the column has no hint, and never doubles a space", () => {
    expect(error(category, "Tools")).toBe("'Tools' is not one of the accepted values for Category (see the Lists sheet).");
    expect(error(category, "twin")).not.toMatch(/\s{2}/);
  });

  it("explains itself when the list was never loaded", () => {
    const result = coerceValue(category, "Hardware", { lookups: {} });
    expect(result).toEqual({
      ok: false,
      error: "The accepted values for Category could not be loaded, so it cannot be checked",
    });
  });
});

describe("coerceRow", () => {
  const entity = makeProductEntity();

  it("returns a parsed row keyed by column with every value typed", () => {
    const { row, errors } = coerceRow(
      entity,
      {
        sku: "  SKU-1 ",
        name: "Steel bracket",
        status: "In stock",
        category: "Hardware",
        price: "1499.50",
        currency: "inr",
        quantity: 12,
        weight: "0.75",
        launched: new Date(Date.UTC(2026, 2, 4)),
        active: "yes",
        email: "Orders@Example.COM",
        phone: "+91 98765 43210",
      },
      ctx,
    );
    expect(errors).toEqual([]);
    expect(row).toEqual({
      sku: "SKU-1",
      name: "Steel bracket",
      status: "in_stock",
      category: "Hardware",
      price: 149950,
      currency: "INR",
      quantity: 12,
      weight: 0.75,
      launched: "2026-03-04",
      active: true,
      email: "Orders@example.com",
      phone: "+919876543210",
    });
  });

  it("gives blank optional cells as null and ignores read-only columns", () => {
    const { row, errors } = coerceRow(entity, { sku: "A", name: "B", id: "should be ignored" }, ctx);
    expect(errors).toEqual([]);
    expect(row.status).toBeNull();
    expect(row.price).toBeNull();
    expect(row).not.toHaveProperty("id");
    expect(row).not.toHaveProperty("created");
  });

  it("collects every column's error, in column order, not just the first", () => {
    const { errors } = coerceRow(
      entity,
      { sku: "", name: "", quantity: "many", launched: "03/04/2026", phone: "12345" },
      ctx,
    );
    expect(errors).toHaveLength(5);
    expect(errors[0]).toBe("SKU is required");
    expect(errors[1]).toBe("Name is required");
    expect(errors[2]).toBe("Quantity must be a whole number");
    expect(errors[3]).toContain("Launch date");
    expect(errors[4]).toContain("Supplier phone");
  });

  it("fills a blank currency with the default, and converts the price by it", () => {
    const { row } = coerceRow(entity, { sku: "A", name: "B", price: "10.55" }, ctx);
    expect(row.currency).toBe("INR");
    expect(row.price).toBe(1055);
  });

  it("converts by the currency the row names", () => {
    expect(coerceRow(entity, { sku: "A", name: "B", price: "500", currency: "JPY" }, ctx).row.price).toBe(500);
    expect(coerceRow(entity, { sku: "A", name: "B", price: "1.234", currency: "KWD" }, ctx).row.price).toBe(1234);
    const refused = coerceRow(entity, { sku: "A", name: "B", price: "1.5", currency: "JPY" }, ctx);
    expect(refused.errors).toEqual(["Price must be a whole number for JPY, which has no decimal places"]);
  });

  it("reports an unusable currency once, not again on the price", () => {
    const { errors, row } = coerceRow(entity, { sku: "A", name: "B", price: "10", currency: "rupees" }, ctx);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain("Currency");
    expect(row.price).toBeNull();
  });

  it("uses a single-currency default when the entity has no currency column", () => {
    const fixed = makeProductEntity({
      columns: [
        { key: "sku", header: "SKU", kind: "text", required: true, help: "The sku." },
        { key: "name", header: "Name", kind: "text", required: true, help: "The name." },
        { key: "price", header: "Price", kind: "money", help: "The price.", defaultCurrency: "JPY" },
      ],
    });
    expect(coerceRow(fixed, { sku: "A", name: "B", price: "250" }, ctx).row.price).toBe(250);
  });

  it("runs the entity's cross-column rule once the columns are clean", () => {
    const rule = { ...entity, validateRow: (row: Record<string, unknown>) => (row.active === true && row.quantity === 0 ? "An active product needs stock" : null) };
    expect(coerceRow(rule, { sku: "A", name: "B", active: "yes", quantity: 0 }, ctx).errors).toEqual([
      "An active product needs stock",
    ]);
    expect(coerceRow(rule, { sku: "A", name: "B", active: "yes", quantity: 3 }, ctx).errors).toEqual([]);
  });

  it("does not run the cross-column rule over a row that already has column errors", () => {
    let called = false;
    const rule = {
      ...entity,
      validateRow: () => {
        called = true;
        return "should not appear";
      },
    };
    expect(coerceRow(rule, { sku: "", name: "B" }, ctx).errors).toEqual(["SKU is required"]);
    expect(called).toBe(false);
  });

  it("takes a RowProblem thrown by the rule as its sentence, and anything else as a generic one", () => {
    const problem = { ...entity, validateRow: () => { throw new RowProblem("Dates are out of order"); } };
    expect(coerceRow(problem, { sku: "A", name: "B" }, ctx).errors).toEqual(["Dates are out of order"]);
    const bug = { ...entity, validateRow: () => { throw new TypeError("boom"); } };
    expect(coerceRow(bug, { sku: "A", name: "B" }, ctx).errors).toEqual(["This row could not be checked"]);
  });

  it("carries an invalid cell into the row's errors", () => {
    const { errors } = coerceRow(
      entity,
      { sku: "A", name: "B", quantity: new InvalidCell("#REF!", "contains the error #REF!") },
      ctx,
    );
    expect(errors).toEqual(["Quantity contains the error #REF!"]);
  });
});
