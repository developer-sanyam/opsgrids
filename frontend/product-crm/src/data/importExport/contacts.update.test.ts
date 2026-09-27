import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, indexExisting, makeLookup, matchRow } from "@opsgrids/client";
import type { LookupSet, ParsedValue } from "@opsgrids/client";

import type { Contact } from "@/api/types";
import { contactImport } from "./contacts";
import { contactWire, contextWith, pageOf, stubApi, stubGateway } from "./entityTestKit";
import {
  OWNER_LOOKUPS,
  notAnAcceptedOwner,
  planFor,
  requestLines,
  runPlan,
  updateSpecOf,
  valuesOf,
} from "./updateTestKit";

const onFile = (...emails: string[]) => makeLookup(emails.map((email) => ({ label: email, value: email })));

const LOOKUPS: LookupSet = {
  ...OWNER_LOOKUPS,
  companies: makeLookup([
    { label: "Northwind Logistics", value: "comp-1" },
    { label: "Cobalt Financial", value: "comp-2" },
  ]),
  // The addresses on file, exactly as stored: this contact's own, one with capitals, and a pair
  // that already differ only in capitals.
  contactEmails: onFile(
    "asha.rao@northwind.co",
    "Priya.Nair@northwind.co",
    "daniel.okafor@cobaltfin.com",
    "Twin@x.co",
    "twin@x.co",
  ),
};
const ctx = contextWith(LOOKUPS);
const spec = updateSpecOf(contactImport);

afterEach(() => {
  vi.unstubAllGlobals();
});

const STORED: Contact = {
  id: "cont-1",
  full_name: "Asha Rao",
  title: "VP Operations",
  email: "asha.rao@northwind.co",
  phone: "+•• •••• ••210",
  company_id: "comp-1",
  status: "customer",
  owner_id: "own-2",
  last_activity_at: null,
  created_at: "2024-02-12T09:00:00Z",
};

/** Every importable cell an export of STORED writes, once coerced. The number is never exported. */
const SAME_AS_STORED: Record<string, ParsedValue> = {
  fullName: "Asha Rao",
  email: "asha.rao@northwind.co",
  company: "Northwind Logistics",
  owner: "Grace@Example.com",
  title: "VP Operations",
  status: "customer",
};

const plan = (cells: Record<string, ParsedValue>, cleared: string[] = [], existing: Contact = STORED) =>
  planFor(contactImport, existing, cells, ctx, cleared);

const PATCH = "PATCH /api/v1/contacts/cont-1";
const PHONE = "PUT /api/v1/contacts/cont-1/phone";
const NEW_PHONE = "+919876543210";

describe("contacts: which record a row names", () => {
  const other: Contact = { ...STORED, id: "cont-2", email: "Priya.Nair@northwind.co" };
  const index = indexExisting(spec, [STORED, other]);

  it("takes the record's own id, and the email as the unique key", () => {
    expect(spec.idOf(STORED)).toBe("cont-1");
    expect(spec.keyColumn).toBe("email");
    expect(spec.keyOf?.(STORED)).toBe("asha.rao@northwind.co");
  });

  it("matches by Contact ID first, and refuses an ID no contact has", () => {
    expect(matchRow(index, "cont-2", "asha.rao@northwind.co")).toMatchObject({ kind: "found", by: "id" });
    expect(matchRow(index, "cont-404", null)).toEqual({ kind: "unknown-id" });
  });

  it("finds a contact by its address even when the sheet spells the capitals differently, and never the wrong one", () => {
    expect(matchRow(index, null, "asha.rao@northwind.co")).toMatchObject({ kind: "found", by: "key", item: STORED });
    expect(matchRow(index, null, "priya.nair@northwind.co")).toMatchObject({ kind: "found", by: "key", item: other });
  });

  it("treats an address no contact has as a new contact", () => {
    expect(matchRow(index, null, "new.person@northwind.co")).toEqual({ kind: "new" });
  });

  it("refuses an address two contacts hold in other capitals (the server allows it), instead of choosing", () => {
    const twins = indexExisting(spec, [
      { ...STORED, id: "cont-8", email: "Twin@x.co" },
      { ...STORED, id: "cont-9", email: "twin@x.co" },
    ]);
    expect(matchRow(twins, null, "Twin@x.co")).toEqual({ kind: "ambiguous" });
  });
});

describe("contacts: what a row changes", () => {
  it("changes nothing, and makes no request, when every cell matches the stored contact", () => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    expect(plan(SAME_AS_STORED)).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("leaves every field alone when its cell is blank, the phone included", () => {
    expect(plan({})).toBeNull();
    expect(plan({ phone: null })).toBeNull();
  });

  const SINGLE: readonly {
    what: string;
    cells: Record<string, ParsedValue>;
    header: string;
    body: Record<string, unknown>;
  }[] = [
    { what: "the name", cells: { fullName: "Asha K. Rao" }, header: "Full name", body: { full_name: "Asha K. Rao" } },
    { what: "the email", cells: { email: "asha@northwind.co" }, header: "Email", body: { email: "asha@northwind.co" } },
    { what: "the company", cells: { company: "cobalt  financial" }, header: "Company", body: { company_id: "comp-2" } },
    { what: "the owner", cells: { owner: "sofia@example.com" }, header: "Owner email", body: { owner_id: "own-1" } },
    { what: "the title", cells: { title: "COO" }, header: "Title", body: { title: "COO" } },
    { what: "the status", cells: { status: "dormant" }, header: "Status", body: { status: "dormant" } },
  ];

  it.each(SINGLE)("sends one PATCH holding only $what, under the key the endpoint names", async ({ cells, header, body }) => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    const result = plan({ ...SAME_AS_STORED, ...cells });
    expect(result?.changed).toEqual([header]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PATCH]);
    expect(calls[0]?.body).toStrictEqual(body);
  });

  it("sends only the fields that differ when several cells change, in one request", async () => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    await runPlan(plan({ ...SAME_AS_STORED, title: "COO", status: "qualified" }));
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body).toStrictEqual({ title: "COO", status: "qualified" });
  });

  it("refuses a company that is not on file, with a sentence naming the column", () => {
    expect(() => plan({ company: "Nowhere Ltd" })).toThrow(RowProblem);
    expect(() => plan({ company: "Nowhere Ltd" })).toThrow(
      "'Nowhere Ltd' is not one of the accepted values for Company (see the Lists sheet). The company must already exist. Import companies first, then contacts and deals. Leave blank to keep the current company.",
    );
  });

  it("refuses an owner nobody has, and a rep who has left when the row gives the contact to them", () => {
    expect(() => plan({ owner: "nobody@example.com" })).toThrow(notAnAcceptedOwner("nobody@example.com"));
    expect(() => plan({ owner: "former@example.com" })).toThrow(notAnAcceptedOwner("former@example.com"));
  });

  it("keeps a contact with the rep who has left when the row repeats them", () => {
    expect(plan({ ...SAME_AS_STORED, owner: "former@example.com" }, [], { ...STORED, owner_id: "own-3" })).toBeNull();
  });

  it("does not pre-empt the server for a caller who may only assign to themselves", async () => {
    stubGateway(() => ({ title: "Forbidden", detail: "You can only assign contacts you own to yourself", status: 403 }), 403);
    await expect(runPlan(plan({ owner: "sofia@example.com" }))).rejects.toMatchObject({
      status: 403,
      message: "You can only assign contacts you own to yourself",
    });
  });
});

describe("contacts: the email", () => {
  it("counts the stored spelling, and the same address in other capitals, as no change", () => {
    expect(plan({ email: "asha.rao@northwind.co" })).toBeNull();
    expect(plan({ email: "Asha.Rao@northwind.co" })).toBeNull();
  });

  it("refuses a new address that differs only in capitals from another contact's, with the sentence used when adding", () => {
    expect(() => plan({ email: "priya.nair@northwind.co" })).toThrow(RowProblem);
    expect(() => plan({ email: "priya.nair@northwind.co" })).toThrow(
      "A contact with this email is already on file as Priya.Nair@northwind.co; addresses that differ only in capitals are treated as the same person.",
    );
  });

  it("refuses one that matches two stored addresses in other capitals", () => {
    expect(() => plan({ email: "TWIN@x.co" })).toThrow(
      "A contact with this email is already on file in other capitals; addresses that differ only in capitals are treated as the same person.",
    );
  });

  it("lets another contact's exact address through, so the server's 409 is what the row reports", async () => {
    const calls = stubGateway(
      () => ({ title: "Conflict", detail: "A contact with that email already exists", status: 409 }),
      409,
    );
    const result = plan({ email: "daniel.okafor@cobaltfin.com" });
    expect(result?.changed).toEqual(["Email"]);
    const failure = await runPlan(result).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, message: "A contact with that email already exists" });
    expect(requestLines(calls)).toEqual([PATCH]);
  });

  it("does not judge the email of a row that leaves it alone, whatever the list holds", () => {
    expect(plan({ ...SAME_AS_STORED, title: "COO" })?.changed).toEqual(["Title"]);
  });
});

describe("contacts: [clear]", () => {
  it("empties the title with an empty string, which the endpoint accepts, never null", async () => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    await runPlan(plan({}, ["title"]));
    expect(calls[0]?.body).toStrictEqual({ title: "" });
    expect(valuesOf(calls[0]?.body)).not.toContain(null);
  });

  it("does nothing to a title that is already empty", () => {
    expect(plan({}, ["title"], { ...STORED, title: "" })).toBeNull();
  });

  it.each([
    ["fullName", "Full name"],
    ["email", "Email"],
    ["company", "Company"],
    ["owner", "Owner email"],
    ["status", "Status"],
  ])("refuses [clear] in %s: the endpoint has no way to empty it", (column, header) => {
    expect(() => plan({}, [column])).toThrow(RowProblem);
    expect(() => plan({}, [column])).toThrow(`${header} cannot be cleared`);
  });

  it("refuses [clear] in Phone: no route removes a saved number", () => {
    expect(() => plan({}, ["phone"])).toThrow(RowProblem);
    expect(() => plan({}, ["phone"])).toThrow("Phone cannot be cleared: a saved number can be replaced, not removed");
  });
});

describe("contacts: the phone number", () => {
  it("writes a filled Phone cell with the write-only phone route, and sends no PATCH when nothing else changed", async () => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    const result = plan({ ...SAME_AS_STORED, phone: NEW_PHONE });
    expect(result?.changed).toEqual(["Phone"]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PHONE]);
    expect(calls[0]?.body).toStrictEqual({ raw_phone: NEW_PHONE });
  });

  it("writes it even when it is the number already on file: the stored one is a mask and cannot be compared", async () => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    // The mask ends in the same three digits as the new number. Nothing is read from it.
    await runPlan(plan({ phone: "+919876543210" }, [], { ...STORED, phone: "+91 98••• ••210" }));
    expect(requestLines(calls)).toEqual([PHONE]);
    expect(JSON.stringify(calls[0]?.body)).not.toContain("•");
  });

  it("does not call the phone route for a blank Phone cell", async () => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    await runPlan(plan({ title: "COO", phone: null }));
    expect(requestLines(calls)).toEqual([PATCH]);
  });

  it("sends the PATCH first and the phone after it, each with only its own data", async () => {
    const calls = stubGateway(() => contactWire("cont-1", "asha.rao@northwind.co", "comp-1"));
    const result = plan({ ...SAME_AS_STORED, title: "COO", phone: NEW_PHONE });
    expect(result?.changed).toEqual(["Title", "Phone"]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PATCH, PHONE]);
    expect(calls[0]?.body).toStrictEqual({ title: "COO" });
    expect(calls[1]?.body).toStrictEqual({ raw_phone: NEW_PHONE });
  });

  it("does not write the phone when the PATCH before it was refused", async () => {
    const calls = stubGateway(
      () => ({ title: "Conflict", detail: "A contact with that email already exists", status: 409 }),
      409,
    );
    await expect(runPlan(plan({ email: "daniel.okafor@cobaltfin.com", phone: NEW_PHONE }))).rejects.toMatchObject({
      status: 409,
    });
    expect(requestLines(calls)).toEqual([PATCH]);
  });

  it("reports a number the server refuses (422) without the number", async () => {
    stubApi(() => ({
      status: 422,
      body: {
        title: "Validation failed",
        status: 422,
        detail: "Request validation failed",
        errors: [{ field: "raw_phone", code: "invalid_phone", message: "That is not a valid phone number with a country code" }],
      },
    }));
    const failure = await runPlan(plan({ phone: NEW_PHONE })).catch((error: unknown) => error);
    if (!(failure instanceof ApiError)) throw new Error("the run should have been refused by the server");
    expect(failure.status).toBe(422);
    expect(failure.message).not.toContain("9876543210");
  });
});

describe("contacts: loadExisting", () => {
  const contacts = (from: number, to: number) =>
    Array.from({ length: to - from + 1 }, (_, i) => contactWire(`cont-${from + i}`, `person${from + i}@northwind.co`, "comp-1"));

  it("reads every page of the whole list, unfiltered, with the dialog's signal", async () => {
    const calls = stubGateway(({ url }) =>
      url.searchParams.get("page") === "1"
        ? pageOf(contacts(1, 200), 1, 200, 201)
        : pageOf(contacts(201, 201), 2, 200, 201),
    );
    const existing = await spec.loadExisting(ctx);
    expect(existing).toHaveLength(201);
    expect(calls.map((c) => [c.url.pathname, c.url.searchParams.get("page"), c.url.searchParams.get("size")])).toEqual([
      ["/api/v1/contacts", "1", "200"],
      ["/api/v1/contacts", "2", "200"],
    ]);
    expect(calls.every((c) => !c.url.searchParams.has("q") && !c.url.searchParams.has("status"))).toBe(true);
    expect(calls.every((c) => c.signal === ctx.signal)).toBe(true);
  });
});
