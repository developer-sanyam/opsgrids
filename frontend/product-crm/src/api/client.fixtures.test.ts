/**
 * Fixture mode (`VITE_USE_FIXTURES=true`, dev builds only) refuses a create the way the server
 * does: with an `ApiError` carrying the status, so that a screen or an import that reads the
 * status behaves as it does against the gateway. An import reports a 409 as "skipped, already
 * exists"; a plain `Error` would have been reported as a failure with a generic sentence.
 *
 * The client reads the flag once, when it loads, so each test loads a fresh copy of it with the flag
 * on. That copy has its own `ApiError` class, so the checks below use the one it was loaded with.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function fixtureClient() {
  vi.stubEnv("VITE_USE_FIXTURES", "true");
  vi.resetModules();
  const client = await import("./client");
  const { ApiError, problemMessage } = await import("@opsgrids/client");
  expect(client.USE_FIXTURES).toBe(true);

  /** What a refused create rejects with, which must be an `ApiError`. */
  async function refusal(work: Promise<unknown>) {
    const error = await work.then(
      () => undefined,
      (thrown: unknown) => thrown,
    );
    if (!(error instanceof ApiError)) throw new Error(`Expected an ApiError, got ${String(error)}`);
    return error;
  }
  return { ...client, problemMessage, refusal };
}

describe("fixture mode: creates are refused like the server refuses them", () => {
  it("a company whose name is on file, ignoring capitals and spaces, is a 409", async () => {
    const { createCompany, problemMessage, refusal } = await fixtureClient();
    const error = await refusal(createCompany({ name: "  NORTHWIND logistics ", owner_id: "own-1" }));
    expect(error.status).toBe(409);
    expect(problemMessage(error)).toBe("A company with that name already exists");
  });

  it("a deal name is compared exactly, capitals included: the same name is a 409, other capitals are a new deal", async () => {
    const { createDeal, problemMessage, refusal } = await fixtureClient();
    const input = {
      company_id: "comp-1",
      owner_id: "own-1",
      value_minor: 100,
      close_date: "2026-12-31",
    };
    const error = await refusal(createDeal({ ...input, name: "Fleet telematics rollout" }));
    expect(error.status).toBe(409);
    expect(problemMessage(error)).toBe("A deal with that name already exists");
    await expect(createDeal({ ...input, name: "FLEET telematics rollout" })).resolves.toMatchObject({
      name: "FLEET telematics rollout",
    });
  });

  it("a contact email is compared exactly: the same address is a 409, other capitals are a new contact", async () => {
    const { createContact, problemMessage, refusal } = await fixtureClient();
    const input = { full_name: "Someone", company_id: "comp-1", owner_id: "own-1" };
    const error = await refusal(createContact({ ...input, email: "rajesh.khanna@northwind.co" }));
    expect(error.status).toBe(409);
    expect(problemMessage(error)).toBe("A contact with that email already exists");
    await expect(createContact({ ...input, email: "Rajesh.Khanna@northwind.co" })).resolves.toMatchObject({
      email: "Rajesh.Khanna@northwind.co",
    });
  });

  it("a contact at a company that does not exist is a 404, and a phone without a country code is a 422 naming the field", async () => {
    const { createContact, problemMessage, refusal } = await fixtureClient();
    const input = { full_name: "Someone", email: "someone@northwind.co", owner_id: "own-1" };
    const missing = await refusal(createContact({ ...input, company_id: "comp-nowhere" }));
    expect(missing.status).toBe(404);
    expect(problemMessage(missing)).toBe("That company is not part of this organization");

    const badPhone = await refusal(createContact({ ...input, company_id: "comp-1", raw_phone: "98765 43210" }));
    expect(badPhone.status).toBe(422);
    expect(badPhone.problem?.errors?.map((e) => e.field)).toEqual(["raw_phone"]);
  });

  it("a lead whose email is on file is a 409, and a phone without a country code is a 422", async () => {
    const { createLead, problemMessage, refusal } = await fixtureClient();
    await createLead({ full_name: "Meera Iyer", email: "meera@halcyontextiles.in" });
    const duplicate = await refusal(createLead({ full_name: "Another Meera", email: "meera@halcyontextiles.in" }));
    expect(duplicate.status).toBe(409);
    expect(problemMessage(duplicate)).toBe("A lead with that email already exists");

    const badPhone = await refusal(createLead({ full_name: "No Code", phone: "090000 12345" }));
    expect(badPhone.status).toBe(422);
    expect(badPhone.problem?.errors?.map((e) => e.field)).toEqual(["phone"]);
  });

  it("a team member whose email is on file is a 409", async () => {
    const { createOwner, problemMessage, refusal } = await fixtureClient();
    const error = await refusal(createOwner({ full_name: "Sofia Again", email: "SOFIA.MARCHETTI@opsgrids.example.com" }));
    expect(error.status).toBe(409);
    expect(problemMessage(error)).toBe("An owner with that email already exists");
  });
});

describe("fixture mode: revealing a sealed value needs no step-up and no gateway", () => {
  const NO_STEP_UP = { Authorization: "" };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reveals a seeded lead's phone and email as plausible values of their own, without a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { revealLeadField } = await fixtureClient();

    expect(await revealLeadField("lead-1", "phone", NO_STEP_UP)).toBe("+91 98765 04821");
    expect(await revealLeadField("lead-1", "email", NO_STEP_UP)).toBe("meera.iyer@solsticeenergy.example");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a lead's missing value the way the server does: a 404 that says what is not on file", async () => {
    const { revealLeadField, problemMessage, refusal } = await fixtureClient();

    const noPhone = await refusal(revealLeadField("lead-2", "phone", NO_STEP_UP));
    expect(noPhone.status).toBe(404);
    expect(problemMessage(noPhone)).toBe("No phone number is on file for this lead");

    const noEmail = await refusal(revealLeadField("lead-4", "email", NO_STEP_UP));
    expect(noEmail.status).toBe(404);
    expect(problemMessage(noEmail)).toBe("No email address is on file for this lead");

    const missing = await refusal(revealLeadField("lead-nowhere", "phone", NO_STEP_UP));
    expect(missing.status).toBe(404);
    expect(problemMessage(missing)).toBe("That lead is not part of this organization");
  });

  it("reveals what was typed for a lead created in this session", async () => {
    const { createLead, revealLeadField } = await fixtureClient();
    const lead = await createLead({ full_name: "Typed Lead", email: "typed@example.com", phone: "+919000012345" });

    expect(await revealLeadField(lead.id, "phone", NO_STEP_UP)).toBe("+919000012345");
    expect(await revealLeadField(lead.id, "email", NO_STEP_UP)).toBe("typed@example.com");
  });

  it("reveals a contact's number as a plausible one that fits its mask, and null for a contact with none", async () => {
    const { revealContactPhone, createContact, refusal } = await fixtureClient();

    expect(await revealContactPhone("con-1", NO_STEP_UP)).toBe("+91 98765 43210");
    const bare = await createContact({
      full_name: "No Number",
      email: "no.number@northwind.co",
      company_id: "comp-1",
      owner_id: "own-1",
    });
    expect(await revealContactPhone(bare.id, NO_STEP_UP)).toBeNull();

    const missing = await refusal(revealContactPhone("con-nowhere", NO_STEP_UP));
    expect(missing.status).toBe(404);
  });
});
