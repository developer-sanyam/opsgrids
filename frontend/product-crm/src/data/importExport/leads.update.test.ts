import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, indexExisting, matchRow } from "@opsgrids/client";
import type { ParsedValue } from "@opsgrids/client";

import type { Lead } from "@/api/types";
import { contextWith, leadWire, pageOf, stubGateway } from "./entityTestKit";
import { leadImport } from "./leads";
import {
  OWNER_LOOKUPS,
  notAnAcceptedOwner,
  planFor,
  requestLines,
  runPlan,
  updateSpecOf,
  valuesOf,
} from "./updateTestKit";

const ctx = contextWith(OWNER_LOOKUPS);
const spec = updateSpecOf(leadImport);

afterEach(() => {
  vi.unstubAllGlobals();
});

const OPEN: Lead = {
  id: "lead-1",
  full_name: "Meera Iyer",
  company: "Halcyon Textiles",
  email: "m•••@h•••.in",
  phone: "+•• ••••• ••345",
  source: "import",
  status: "working",
  owner_id: "own-2",
  notes: "Asked for a demo.",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-09-20T09:00:00Z",
};

const CONVERTED: Lead = {
  ...OPEN,
  id: "lead-2",
  status: "converted",
  converted_company_id: "comp-1",
  converted_contact_id: "cont-1",
  converted_deal_id: "deal-1",
  converted_at: "2026-09-25T09:00:00Z",
};

/** Every importable cell an export of OPEN writes, once coerced. Email and phone are never exported. */
const SAME_AS_STORED: Record<string, ParsedValue> = {
  fullName: "Meera Iyer",
  company: "Halcyon Textiles",
  owner: "Grace@Example.com",
  notes: "Asked for a demo.",
};

const plan = (cells: Record<string, ParsedValue>, cleared: string[] = [], existing: Lead = OPEN) =>
  planFor(leadImport, existing, cells, ctx, cleared);

const PATCH = "PATCH /api/v1/leads/lead-1";

describe("leads: which record a row names", () => {
  const index = indexExisting(spec, [OPEN, CONVERTED]);

  it("takes the record's own id, and has no key: the email is sealed, so a lead is found by its Lead ID only", () => {
    expect(spec.idOf(OPEN)).toBe("lead-1");
    expect(spec.keyColumn).toBeUndefined();
    expect(spec.keyOf).toBeUndefined();
  });

  it("matches by Lead ID, and refuses an ID no lead has", () => {
    expect(matchRow(index, "lead-2", null)).toMatchObject({ kind: "found", by: "id", item: CONVERTED });
    expect(matchRow(index, "lead-404", null)).toEqual({ kind: "unknown-id" });
  });

  it("treats a row without a Lead ID as a new lead, whatever else it says", () => {
    expect(matchRow(index, null, null)).toEqual({ kind: "new" });
    expect(index.byKey.size).toBe(0);
  });
});

describe("leads: what a row changes", () => {
  it("changes nothing, and makes no request, when every cell matches the stored lead", () => {
    const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
    expect(plan(SAME_AS_STORED)).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("leaves every field alone when its cell is blank", () => {
    expect(plan({})).toBeNull();
    expect(plan({ email: null, phone: null })).toBeNull();
  });

  const SINGLE: readonly {
    what: string;
    cells: Record<string, ParsedValue>;
    header: string;
    body: Record<string, unknown>;
  }[] = [
    { what: "the name", cells: { fullName: "Meera Iyer-Nair" }, header: "Full name", body: { full_name: "Meera Iyer-Nair" } },
    { what: "the company", cells: { company: "Halcyon Mills" }, header: "Company", body: { company: "Halcyon Mills" } },
    { what: "the owner", cells: { owner: "sofia@example.com" }, header: "Owner email", body: { owner_id: "own-1" } },
    { what: "the notes", cells: { notes: "Asked for a demo twice." }, header: "Notes", body: { notes: "Asked for a demo twice." } },
  ];

  it.each(SINGLE)("sends one PATCH holding only $what, under the key the endpoint names", async ({ cells, header, body }) => {
    const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
    const result = plan({ ...SAME_AS_STORED, ...cells });
    expect(result?.changed).toEqual([header]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual([PATCH]);
    expect(calls[0]?.body).toStrictEqual(body);
  });

  it("assigns an owner to a lead that has none, and sets a company on one that has none", async () => {
    const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
    await runPlan(plan({ owner: "sofia@example.com", company: "Halcyon Mills" }, [], { ...OPEN, owner_id: null, company: null }));
    expect(calls[0]?.body).toStrictEqual({ company: "Halcyon Mills", owner_id: "own-1" });
  });

  it("refuses an owner nobody has, and a rep who has left when the row gives the lead to them", () => {
    expect(() => plan({ owner: "nobody@example.com" })).toThrow(RowProblem);
    expect(() => plan({ owner: "nobody@example.com" })).toThrow(notAnAcceptedOwner("nobody@example.com"));
    expect(() => plan({ owner: "former@example.com" })).toThrow(notAnAcceptedOwner("former@example.com"));
  });

  it("keeps a lead with the rep who has left when the row repeats them", () => {
    expect(plan({ ...SAME_AS_STORED, owner: "former@example.com" }, [], { ...OPEN, owner_id: "own-3" })).toBeNull();
  });

  it("sends the notes as the sheet holds them, line breaks and all", async () => {
    const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
    await runPlan(plan({ notes: "Asked for a demo.\nCall back Monday." }));
    expect(calls[0]?.body).toStrictEqual({ notes: "Asked for a demo.\nCall back Monday." });
  });

  it("does not count a note as changed for the ends of its text or its line endings, which a sheet does not keep", () => {
    const stored = { ...OPEN, notes: "First line.\r\nSecond line.  \n" };
    expect(plan({ notes: "First line.\nSecond line." }, [], stored)).toBeNull();
  });
});

describe("leads: [clear]", () => {
  it("empties the company with null, the one field of a lead the endpoint empties that way", async () => {
    const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
    await runPlan(plan({}, ["company"]));
    expect(calls[0]?.body).toStrictEqual({ company: null });
  });

  it("empties the notes with an empty string, because the endpoint refuses null there", async () => {
    const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
    await runPlan(plan({}, ["notes"]));
    expect(calls[0]?.body).toStrictEqual({ notes: "" });
    expect(valuesOf(calls[0]?.body)).not.toContain(null);
  });

  it("does nothing to a field that is already empty", () => {
    expect(plan({}, ["company", "notes"], { ...OPEN, company: null, notes: "" })).toBeNull();
  });

  it("never unassigns an owner from a sheet, and never empties a name", () => {
    expect(() => plan({}, ["owner"])).toThrow(RowProblem);
    expect(() => plan({}, ["owner"])).toThrow("Owner email cannot be cleared");
    expect(() => plan({}, ["fullName"])).toThrow("Full name cannot be cleared");
  });
});

describe("leads: email and phone have no update route", () => {
  it("refuses a filled Email cell rather than ignoring it", () => {
    expect(() => plan({ email: "meera@halcyontextiles.in" })).toThrow(RowProblem);
    expect(() => plan({ email: "meera@halcyontextiles.in" })).toThrow(
      "Email cannot be changed by an update: leave the cell blank.",
    );
  });

  it("refuses a filled Phone cell", () => {
    expect(() => plan({ phone: "+919000012345" })).toThrow(
      "Phone cannot be changed by an update: leave the cell blank.",
    );
  });

  it("refuses them for a lead that has none on file too: there is no way to add one either", () => {
    expect(() => plan({ email: "meera@halcyontextiles.in" }, [], { ...OPEN, email: null })).toThrow(
      "Email cannot be changed by an update: leave the cell blank.",
    );
  });

  it("refuses [clear] in either, and names both when both are filled", () => {
    expect(() => plan({}, ["email"])).toThrow("Email cannot be changed by an update");
    expect(() => plan({}, ["phone"])).toThrow("Phone cannot be changed by an update");
    expect(() => plan({ email: "a@b.co", phone: "+911234567890" })).toThrow(
      "Email, Phone cannot be changed by an update: leave the cell blank.",
    );
  });

  it("sends no request for a row the refusal stopped, even one that changes another field", () => {
    const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
    expect(() => plan({ fullName: "Someone Else", email: "meera@halcyontextiles.in" })).toThrow(RowProblem);
    expect(calls).toHaveLength(0);
  });

  it("does not offer the status or the source to a sheet: they are read-only columns", () => {
    for (const key of ["status", "source"]) {
      expect(leadImport.columns.find((c) => c.key === key)?.readOnly).toBe(true);
      expect(spec.fields.map((f) => f.column)).not.toContain(key);
    }
  });
});

describe("leads: a converted lead", () => {
  it("refuses an owner change, before anything is sent", () => {
    const calls = stubGateway(() => leadWire("lead-2", "Meera Iyer"));
    expect(() => plan({ owner: "sofia@example.com" }, [], CONVERTED)).toThrow(RowProblem);
    expect(() => plan({ owner: "sofia@example.com" }, [], CONVERTED)).toThrow(
      "A converted lead cannot be reassigned. Its Full name, Company and Notes can still be changed.",
    );
    expect(calls).toHaveLength(0);
  });

  it("still changes the name, company and notes", async () => {
    const calls = stubGateway(() => leadWire("lead-2", "Meera Iyer"));
    const result = plan({ fullName: "M. Iyer", company: "Halcyon Mills", notes: "Won." }, [], CONVERTED);
    expect(result?.changed).toEqual(["Full name", "Company", "Notes"]);
    await runPlan(result);
    expect(requestLines(calls)).toEqual(["PATCH /api/v1/leads/lead-2"]);
    expect(calls[0]?.body).toStrictEqual({ full_name: "M. Iyer", company: "Halcyon Mills", notes: "Won." });
  });

  it("never sends the owner key on a converted lead whose owner cell repeats what is stored: the server refuses even an unchanged one", async () => {
    const calls = stubGateway(() => leadWire("lead-2", "Meera Iyer"));
    await runPlan(plan({ owner: "Grace@Example.com", notes: "Won." }, [], CONVERTED));
    expect(calls[0]?.body).toStrictEqual({ notes: "Won." });
  });

  it("makes no request for a converted lead whose row changes nothing", () => {
    expect(plan(SAME_AS_STORED, [], CONVERTED)).toBeNull();
  });

  // The server refuses on either sign (`services.update_lead`: the status or `converted_company_id`),
  // and the two are meant to agree, so a lead that only shows one is still not reassigned.
  describe("that only shows one sign of it", () => {
    const POINTS_AT_ITS_COMPANY: Lead = { ...OPEN, converted_company_id: "comp-1" };

    it("refuses an owner change on a lead that points at the company it became, whatever its status says", () => {
      const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
      expect(() => plan({ owner: "sofia@example.com" }, [], POINTS_AT_ITS_COMPANY)).toThrow(
        "A converted lead cannot be reassigned. Its Full name, Company and Notes can still be changed.",
      );
      expect(calls).toHaveLength(0);
    });

    it("still changes its name, company and notes", async () => {
      const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
      await runPlan(plan({ fullName: "M. Iyer", notes: "Won." }, [], POINTS_AT_ITS_COMPANY));
      expect(calls[0]?.body).toStrictEqual({ full_name: "M. Iyer", notes: "Won." });
    });

    it("refuses an owner change on a lead whose status says converted and that points at nothing", () => {
      expect(() => plan({ owner: "sofia@example.com" }, [], { ...OPEN, status: "converted" })).toThrow(
        "A converted lead cannot be reassigned",
      );
    });

    it("does not refuse the owner of a lead that shows neither", async () => {
      const calls = stubGateway(() => leadWire("lead-1", "Meera Iyer"));
      await runPlan(plan({ owner: "sofia@example.com" }));
      expect(calls[0]?.body).toStrictEqual({ owner_id: "own-1" });
    });
  });
});

describe("leads: how a row names its lead in the preview", () => {
  it("uses the name and the company as the lead gave them", () => {
    expect(spec.recordLabel?.(OPEN)).toBe("Meera Iyer (Halcyon Textiles)");
  });

  it("uses the name alone for a lead with no company", () => {
    expect(spec.recordLabel?.({ ...OPEN, company: null })).toBe("Meera Iyer");
    expect(spec.recordLabel?.({ ...OPEN, company: "" })).toBe("Meera Iyer");
  });

  it("never shows the email or the phone, masked or not: they are sealed", () => {
    const label = spec.recordLabel?.({ ...OPEN, email: "meera@halcyontextiles.in", phone: "+919000012345" }) ?? "";
    expect(label).not.toMatch(/@|\d{5}|•/);
  });
});

describe("leads: what the server says", () => {
  it("reports a refusal as a failure with the server's own sentence", async () => {
    stubGateway(
      () => ({ title: "Conflict", detail: "A conversion of this lead is in progress; try again shortly", status: 409 }),
      409,
    );
    const failure = await runPlan(plan({ notes: "Changed" })).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, message: "A conversion of this lead is in progress; try again shortly" });
  });
});

describe("leads: loadExisting", () => {
  const leads = (from: number, to: number) =>
    Array.from({ length: to - from + 1 }, (_, i) => leadWire(`lead-${from + i}`, `Lead ${from + i}`));

  it("reads every page of the whole list, every status included, with the dialog's signal", async () => {
    const calls = stubGateway(({ url }) =>
      url.searchParams.get("page") === "1" ? pageOf(leads(1, 200), 1, 200, 201) : pageOf(leads(201, 201), 2, 200, 201),
    );
    const existing = await spec.loadExisting(ctx);
    expect(existing).toHaveLength(201);
    expect(calls.map((c) => [c.url.pathname, c.url.searchParams.get("page"), c.url.searchParams.get("size")])).toEqual([
      ["/api/v1/leads", "1", "200"],
      ["/api/v1/leads", "2", "200"],
    ]);
    expect(calls.every((c) => !c.url.searchParams.has("status") && !c.url.searchParams.has("owner_id"))).toBe(true);
    expect(calls.every((c) => c.signal === ctx.signal)).toBe(true);
  });

  it("carries the converted state through, which the owner rule reads", async () => {
    stubGateway(() => pageOf([leadWire("lead-9", "Won Lead", { status: "converted", converted_at: "2026-09-25T09:00:00Z" })]));
    const [won] = await spec.loadExisting(ctx);
    expect(won?.status).toBe("converted");
  });
});
