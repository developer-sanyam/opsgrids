import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, indexExisting, makeLookup, matchRow } from "@opsgrids/client";
import type { LookupSet, ParsedValue } from "@opsgrids/client";

import type { Deal } from "@/api/types";
import { dealImport } from "./deals";
import {
  OWNERS_WIRE,
  companyWire,
  contactWire,
  contextWith,
  dealWire,
  pageOf,
  present,
  stubApi,
  stubGateway,
} from "./entityTestKit";
import { planFor, requestLines, runPlan, updateSpecOf, valuesOf } from "./updateTestKit";

const LOOKUPS: LookupSet = {
  owners: makeLookup([
    { label: "sofia@example.com", value: "own-1" },
    { label: "Grace@Example.com", value: "own-2" },
  ]),
  companies: makeLookup([
    { label: "Northwind Logistics", value: "comp-1" },
    { label: "Cobalt Financial", value: "comp-2" },
  ]),
  contacts: makeLookup([
    { label: "asha@northwind.co", value: "cont-1" },
    { label: "priya@northwind.co", value: "cont-3" },
    { label: "daniel@cobaltfin.com", value: "cont-2" },
  ]),
  contactCompanies: makeLookup([
    { label: "asha@northwind.co", value: "comp-1" },
    { label: "priya@northwind.co", value: "comp-1" },
    { label: "daniel@cobaltfin.com", value: "comp-2" },
  ]),
  companyOfContact: makeLookup([
    { label: "cont-1", value: "comp-1" },
    { label: "cont-3", value: "comp-1" },
    { label: "cont-2", value: "comp-2" },
  ]),
};
const ctx = contextWith(LOOKUPS);
const spec = updateSpecOf(dealImport);

afterEach(() => {
  vi.unstubAllGlobals();
});

const STORED: Deal = {
  id: "deal-1",
  name: "Northwind fleet renewal",
  company_id: "comp-1",
  primary_contact_id: "cont-1",
  value_minor: 320000000,
  currency: "INR",
  stage: "proposal",
  owner_id: "own-2",
  probability: 55,
  close_date: "2026-12-15",
  created_at: "2026-09-27T09:00:00Z",
  last_activity_at: null,
  source: "Referral",
};

/** Every importable cell an export of STORED writes, once coerced. */
const SAME_AS_STORED: Record<string, ParsedValue> = {
  name: "Northwind fleet renewal",
  company: "Northwind Logistics",
  contact: "asha@northwind.co",
  owner: "Grace@Example.com",
  value: 320000000,
  currency: "INR",
  closeDate: "2026-12-15",
  stage: "proposal",
  probability: 55,
  source: "Referral",
};

const plan = (cells: Record<string, ParsedValue>, cleared: string[] = [], existing: Deal = STORED) =>
  planFor(dealImport, existing, cells, ctx, cleared);

const PATCH = "PATCH /api/v1/deals/deal-1";
const STAGE = "POST /api/v1/deals/deal-1/stage";
const answer = () => dealWire("deal-1", "Northwind fleet renewal");

describe("deals: which record a row names", () => {
  const other: Deal = { ...STORED, id: "deal-2", name: "Cobalt treasury platform" };
  const index = indexExisting(spec, [STORED, other]);

  it("takes the record's own id, and the name as the key", () => {
    expect(spec.idOf(STORED)).toBe("deal-1");
    expect(spec.keyColumn).toBe("name");
    expect(spec.keyOf?.(STORED)).toBe("Northwind fleet renewal");
  });

  it("matches by Deal ID first, and refuses an ID no deal has", () => {
    expect(matchRow(index, "deal-2", "Northwind fleet renewal")).toMatchObject({ kind: "found", by: "id" });
    expect(matchRow(index, "deal-404", null)).toEqual({ kind: "unknown-id" });
  });

  it("finds a deal by name, and treats an unknown name as a new deal", () => {
    expect(matchRow(index, null, "Northwind fleet renewal")).toMatchObject({ kind: "found", by: "key", item: STORED });
    expect(matchRow(index, null, "Brand new deal")).toEqual({ kind: "new" });
  });

  it("refuses a name two deals share (nothing in the database stops it), instead of choosing one", () => {
    const twins = indexExisting(spec, [STORED, { ...other, name: "Northwind fleet renewal" }]);
    expect(matchRow(twins, null, "Northwind fleet renewal")).toEqual({ kind: "ambiguous" });
  });
});

describe("deals: what a row changes", () => {
  it("changes nothing, and makes no request, when every cell matches the stored deal", () => {
    const calls = stubGateway(answer);
    expect(plan(SAME_AS_STORED)).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("leaves every field alone when its cell is blank", () => {
    expect(plan({})).toBeNull();
  });

  const SINGLE: readonly {
    what: string;
    cells: Record<string, ParsedValue>;
    header: string;
    body: Record<string, unknown>;
  }[] = [
    { what: "the name", cells: { name: "Northwind fleet renewal 2027" }, header: "Deal name", body: { name: "Northwind fleet renewal 2027" } },
    { what: "the primary contact, within the same company", cells: { contact: "priya@northwind.co" }, header: "Primary contact email", body: { primary_contact_id: "cont-3" } },
    { what: "the owner", cells: { owner: "sofia@example.com" }, header: "Owner email", body: { owner_id: "own-1" } },
    { what: "the value, in minor units", cells: { value: 450000000 }, header: "Deal value", body: { value_minor: 450000000 } },
    { what: "the close date", cells: { closeDate: "2027-01-31" }, header: "Close date", body: { close_date: "2027-01-31" } },
    { what: "the probability", cells: { probability: 70 }, header: "Probability (%)", body: { probability: 70 } },
    { what: "the source", cells: { source: "Outbound" }, header: "Source", body: { source: "Outbound" } },
  ];

  it.each(SINGLE)("sends one PATCH holding only $what, under the key the endpoint names", async ({ cells, header, body }) => {
    const calls = stubGateway(answer);
    const result = plan({ ...SAME_AS_STORED, ...cells });
    expect(result?.changed).toEqual([header]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PATCH]);
    expect(calls[0]?.body).toStrictEqual(body);
  });

  it("sends only the fields that differ when several cells change, in one request", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ ...SAME_AS_STORED, value: 450000000, source: "Outbound", closeDate: "2027-02-28" }));
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body).toStrictEqual({ value_minor: 450000000, close_date: "2027-02-28", source: "Outbound" });
  });

  it("changes the probability of a deal on its own with a PATCH, and no stage move", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ ...SAME_AS_STORED, probability: 0 }));
    expect(requestLines(calls)).toEqual([PATCH]);
    expect(calls[0]?.body).toStrictEqual({ probability: 0 });
  });
});

describe("deals: [clear]", () => {
  it("removes the primary contact with null, the one field of a deal the endpoint empties that way", async () => {
    const calls = stubGateway(answer);
    const result = plan({}, ["contact"]);
    expect(result?.changed).toEqual(["Primary contact email"]);
    await runPlan(result);
    expect(calls[0]?.body).toStrictEqual({ primary_contact_id: null });
  });

  it("empties the source with an empty string, because the endpoint refuses null there", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({}, ["source"]));
    expect(calls[0]?.body).toStrictEqual({ source: "" });
    expect(valuesOf(calls[0]?.body)).not.toContain(null);
  });

  it("does nothing to a field that is already empty", () => {
    expect(plan({}, ["contact", "source"], { ...STORED, primary_contact_id: null, source: "" })).toBeNull();
  });

  it.each([
    ["name", "Deal name"],
    ["company", "Company"],
    ["owner", "Owner email"],
    ["value", "Deal value"],
    ["currency", "Currency"],
    ["closeDate", "Close date"],
    ["stage", "Stage"],
    ["probability", "Probability (%)"],
  ])("refuses [clear] in %s: it has no empty value", (column, header) => {
    expect(() => plan({}, [column])).toThrow(RowProblem);
    expect(() => plan({}, [column])).toThrow(`${header} cannot be cleared`);
  });
});

describe("deals: the stage", () => {
  it("moves the stage through its own endpoint, with no PATCH when nothing else changed and no probability the row did not change", async () => {
    const calls = stubGateway(answer);
    const result = plan({ ...SAME_AS_STORED, stage: "negotiation" });
    expect(result?.changed).toEqual(["Stage"]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([STAGE]);
    expect(calls[0]?.body).toStrictEqual({ stage: "negotiation" });
  });

  it("sends the PATCH first and the stage move after it, the PATCH holding neither stage nor probability", async () => {
    const calls = stubGateway(answer);
    const result = plan({ ...SAME_AS_STORED, stage: "negotiation", value: 450000000, source: "Outbound" });
    expect(result?.changed).toEqual(["Deal value", "Stage", "Source"]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PATCH, STAGE]);
    expect(calls[0]?.body).toStrictEqual({ value_minor: 450000000, source: "Outbound" });
    expect(calls[1]?.body).toStrictEqual({ stage: "negotiation" });
  });

  it("carries a probability the row changes on the stage move, not in the PATCH, so the stage does not reset it", async () => {
    const calls = stubGateway(answer);
    const result = plan({ ...SAME_AS_STORED, stage: "negotiation", probability: 70, value: 450000000 });
    expect(result?.changed).toEqual(["Deal value", "Stage", "Probability (%)"]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PATCH, STAGE]);
    expect(calls[0]?.body).toStrictEqual({ value_minor: 450000000 });
    expect(calls[1]?.body).toStrictEqual({ stage: "negotiation", probability: 70 });
  });

  it("makes no PATCH at all for a row that changes only the stage and the probability", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ ...SAME_AS_STORED, stage: "closed_won", probability: 100 }));
    expect(requestLines(calls)).toEqual([STAGE]);
    expect(calls[0]?.body).toStrictEqual({ stage: "closed_won", probability: 100 });
  });

  it("lets the stage set its usual probability when the row repeats the one on file, as a move on the board does", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ ...SAME_AS_STORED, stage: "closed_won" }));
    expect(calls[0]?.body).toStrictEqual({ stage: "closed_won" });
  });

  it("does not move a deal to the stage it is already in", () => {
    expect(plan({ ...SAME_AS_STORED, stage: "proposal" })).toBeNull();
  });

  it("leaves the stage alone when its cell is blank", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ ...SAME_AS_STORED, stage: null, source: "Outbound" }));
    expect(requestLines(calls)).toEqual([PATCH]);
  });

  it("refuses a stage change on a closed deal before anything is sent, and says which stage it is in", () => {
    const calls = stubGateway(answer);
    const won: Deal = { ...STORED, stage: "closed_won", probability: 100 };
    expect(() => plan({ stage: "lead" }, [], won)).toThrow(RowProblem);
    expect(() => plan({ stage: "lead" }, [], won)).toThrow(
      "This deal is Closed Won, and a closed deal cannot be moved to another stage.",
    );
    expect(() => plan({ stage: "proposal", value: 1 }, [], { ...won, stage: "closed_lost" })).toThrow("This deal is Closed Lost");
    expect(calls).toHaveLength(0);
  });

  it("still changes the other fields of a closed deal, and lets its stage cell repeat what is stored", async () => {
    const calls = stubGateway(answer);
    const won: Deal = { ...STORED, stage: "closed_won", probability: 100 };
    await runPlan(plan({ stage: "closed_won", source: "Outbound", probability: 100 }, [], won));
    expect(requestLines(calls)).toEqual([PATCH]);
    expect(calls[0]?.body).toStrictEqual({ source: "Outbound" });
  });

  it("does not move the stage when the PATCH before it was refused", async () => {
    const calls = stubGateway(() => ({ title: "Conflict", detail: "A deal with that name already exists", status: 409 }), 409);
    await expect(runPlan(plan({ name: "Cobalt treasury platform", stage: "negotiation" }))).rejects.toMatchObject({
      status: 409,
      message: "A deal with that name already exists",
    });
    expect(requestLines(calls)).toEqual([PATCH]);
  });

  it("reports the server's refusal of a stage move as the row's failure", async () => {
    const calls = stubApi(({ method }) =>
      method === "PATCH"
        ? { body: answer() }
        : { status: 409, body: { title: "Conflict", detail: "A deal in 'proposal' cannot move to 'lead'", status: 409 } },
    );
    const failure = await runPlan(plan({ source: "Outbound", stage: "lead" })).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, message: "A deal in 'proposal' cannot move to 'lead'" });
    expect(requestLines(calls)).toEqual([PATCH, STAGE]);
  });
});

describe("deals: owner, company and primary contact are checked here, because the server does not", () => {
  it("refuses an owner who is not an active rep", () => {
    expect(() => plan({ owner: "nobody@example.com" })).toThrow(RowProblem);
    expect(() => plan({ owner: "nobody@example.com" })).toThrow("'nobody@example.com' is not an active sales rep.");
  });

  it("refuses a company that is not on file", () => {
    expect(() => plan({ company: "Nowhere Ltd" })).toThrow(
      "'Nowhere Ltd' is not a company on file. Use one from the Lists sheet, or leave Company blank to keep it as it is.",
    );
  });

  it("refuses a contact that is not on file", () => {
    expect(() => plan({ contact: "ghost@northwind.co" })).toThrow(
      "'ghost@northwind.co' is not a contact on file. Use one from the Lists sheet, or leave Primary contact email blank to keep it as it is.",
    );
  });

  it("refuses a contact who works at another company, with the sentence used when adding", () => {
    expect(() => plan({ contact: "daniel@cobaltfin.com" })).toThrow(
      "daniel@cobaltfin.com is not a contact of Northwind Logistics. Pick one of that company's contacts.",
    );
  });

  it("moves a deal to another company together with one of that company's contacts", async () => {
    const calls = stubGateway(answer);
    const result = plan({ company: "Cobalt Financial", contact: "daniel@cobaltfin.com" });
    expect(result?.changed).toEqual(["Company", "Primary contact email"]);
    await runPlan(result);
    expect(calls[0]?.body).toStrictEqual({ company_id: "comp-2", primary_contact_id: "cont-2" });
  });

  it("moves a deal to another company and removes its contact with [clear]", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ company: "Cobalt Financial" }, ["contact"]));
    expect(calls[0]?.body).toStrictEqual({ company_id: "comp-2", primary_contact_id: null });
  });

  it("refuses a move to another company that would leave the old company's contact on the deal", () => {
    expect(() => plan({ company: "Cobalt Financial" })).toThrow(RowProblem);
    expect(() => plan({ company: "Cobalt Financial" })).toThrow(
      "asha@northwind.co is the deal's primary contact but is not a contact of Cobalt Financial. Name one of that company's contacts in Primary contact email, or type [clear] to remove the primary contact.",
    );
  });

  it("refuses a move that names the old contact again, as it would when adding", () => {
    expect(() => plan({ company: "Cobalt Financial", contact: "asha@northwind.co" })).toThrow(
      "asha@northwind.co is not a contact of Cobalt Financial. Pick one of that company's contacts.",
    );
  });

  it("moves a deal that has no contact to another company", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ company: "Cobalt Financial" }, [], { ...STORED, primary_contact_id: null }));
    expect(calls[0]?.body).toStrictEqual({ company_id: "comp-2" });
  });

  it("does not judge a row that touches neither, on a contact stored against another company", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ value: 450000000 }, [], { ...STORED, primary_contact_id: "cont-2" }));
    expect(calls[0]?.body).toStrictEqual({ value_minor: 450000000 });
  });

  it("cannot judge a stored contact the lists do not hold, so it leaves the company move to the server", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ company: "Cobalt Financial" }, [], { ...STORED, primary_contact_id: "cont-gone" }));
    expect(calls[0]?.body).toStrictEqual({ company_id: "comp-2" });
  });

  it("does not pre-empt the server for a caller who may only assign to themselves", async () => {
    stubGateway(() => ({ title: "Forbidden", detail: "You can only assign deals you own to yourself", status: 403 }), 403);
    await expect(runPlan(plan({ owner: "sofia@example.com" }))).rejects.toMatchObject({
      status: 403,
      message: "You can only assign deals you own to yourself",
    });
  });
});

describe("deals: money and currency", () => {
  const DINARS: Deal = { ...STORED, currency: "KWD", value_minor: 5250 };

  it("names the stored currency, so a blank Currency cell is read in it and not in INR", () => {
    expect(spec.currencyOf?.(DINARS)).toBe("KWD");
    expect(spec.currencyOf?.(STORED)).toBe("INR");
  });

  it("leaves a blank Currency cell alone: a dinar deal is not re-priced in rupees", async () => {
    const calls = stubGateway(answer);
    // The engine has read the amount in the stored currency (three decimals): only the amount differs.
    await runPlan(plan({ value: 6000 }, [], DINARS));
    expect(calls[0]?.body).toStrictEqual({ value_minor: 6000 });
  });

  it("changes nothing when the value and the currency repeat what is stored", () => {
    expect(plan({ value: 5250, currency: "kwd" }, [], DINARS)).toBeNull();
    expect(plan({ value: 5250 }, [], DINARS)).toBeNull();
  });

  it("sends the currency and the value together when the row restates both", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ value: 45000, currency: "usd" }));
    expect(calls[0]?.body).toStrictEqual({ value_minor: 45000, currency: "USD" });
  });

  it("refuses a currency change with no value: the server would re-label the value on file", () => {
    expect(() => plan({ currency: "USD" })).toThrow(RowProblem);
    expect(() => plan({ currency: "USD" })).toThrow(
      "Currency cannot change on its own: Deal value is not converted, so the amount on file would be read in the new currency. Fill in Deal value in USD too.",
    );
  });

  it("allows a currency change on a deal worth nothing", async () => {
    const calls = stubGateway(answer);
    await runPlan(plan({ currency: "USD" }, [], { ...STORED, value_minor: 0 }));
    expect(calls[0]?.body).toStrictEqual({ currency: "USD" });
  });
});

describe("deals: what the server says", () => {
  it("reports a rename onto another deal's name as a failure, never as skipped", async () => {
    stubGateway(() => ({ title: "Conflict", detail: "A deal with that name already exists", status: 409 }), 409);
    const failure = await runPlan(plan({ name: "Cobalt treasury platform" })).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, message: "A deal with that name already exists" });
  });
});

describe("deals: loadLookups", () => {
  it("also holds each contact's company by the contact's id, which is how a stored deal names its contact", async () => {
    stubGateway(({ url }) => {
      if (url.pathname.endsWith("/customers/owners")) return OWNERS_WIRE;
      if (url.pathname.endsWith("/customers")) return pageOf([companyWire("comp-1", "Northwind Logistics")]);
      return pageOf([contactWire("cont-1", "asha@northwind.co", "comp-1")]);
    });
    const lookups = await present(dealImport.loadLookups, "loadLookups")(new AbortController().signal);
    expect(lookups.companyOfContact?.resolve("cont-1")).toBe("comp-1");
    expect(lookups.companyOfContact?.resolve("cont-404")).toBeUndefined();
  });

  it("keeps that lookup off every column, so it never reaches the Lists sheet or a dropdown", () => {
    expect(dealImport.columns.some((c) => c.lookup === "companyOfContact")).toBe(false);
  });
});

describe("deals: loadExisting", () => {
  const deals = (from: number, to: number) =>
    Array.from({ length: to - from + 1 }, (_, i) => dealWire(`deal-${from + i}`, `Deal ${from + i}`));

  it("reads every page of the whole list, unfiltered, with the dialog's signal", async () => {
    const calls = stubGateway(({ url }) =>
      url.searchParams.get("page") === "1" ? pageOf(deals(1, 200), 1, 200, 201) : pageOf(deals(201, 201), 2, 200, 201),
    );
    const existing = await spec.loadExisting(ctx);
    expect(existing).toHaveLength(201);
    expect(calls.map((c) => [c.url.pathname, c.url.searchParams.get("page"), c.url.searchParams.get("size")])).toEqual([
      ["/api/v1/deals", "1", "200"],
      ["/api/v1/deals", "2", "200"],
    ]);
    expect(
      calls.every((c) => !c.url.searchParams.has("q") && !c.url.searchParams.has("stage") && !c.url.searchParams.has("owner_id")),
    ).toBe(true);
    expect(calls.every((c) => c.signal === ctx.signal)).toBe(true);
  });
});
