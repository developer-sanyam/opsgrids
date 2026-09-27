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

/**
 * The same for updates (the bulk update from Excel reads the status: a 409 on a rename is a real
 * failure the user must see, and a record that has gone is a 404), and with the server's sentences.
 */
describe("fixture mode: updates are refused like the server refuses them", () => {
  it("a company that does not exist is a 404", async () => {
    const { updateCompany, problemMessage, refusal } = await fixtureClient();
    const error = await refusal(updateCompany("comp-nowhere", { city: "Pune" }));
    expect(error.status).toBe(404);
    expect(problemMessage(error)).toBe("That company is not part of this organization");
  });

  it("a company renamed onto another's name, ignoring capitals and spaces, is a 409; a change of its own capitals is a rename", async () => {
    const { updateCompany, problemMessage, refusal } = await fixtureClient();
    const error = await refusal(updateCompany("comp-1", { name: "  MERIDIAN steelworks " }));
    expect(error.status).toBe(409);
    expect(problemMessage(error)).toBe("A company with that name already exists");
    await expect(updateCompany("comp-1", { name: "NORTHWIND LOGISTICS" })).resolves.toMatchObject({
      name: "NORTHWIND LOGISTICS",
    });
  });

  it("a deal renamed onto another's exact name is a 409, other capitals are a new name, and a missing deal is a 404", async () => {
    const { updateDeal, problemMessage, refusal } = await fixtureClient();
    const clash = await refusal(updateDeal("deal-2", { name: "Fleet telematics rollout" }));
    expect(clash.status).toBe(409);
    // The server names the deal on an update (`services.update_deal`), unlike on a create.
    expect(problemMessage(clash)).toBe("A deal named 'Fleet telematics rollout' already exists");
    await expect(updateDeal("deal-2", { name: "FLEET telematics rollout" })).resolves.toMatchObject({
      name: "FLEET telematics rollout",
    });
    expect((await refusal(updateDeal("deal-nowhere", { source: "x" }))).status).toBe(404);
  });

  it("a contact moved onto another's exact email is a 409, other capitals are allowed as the server allows them, and a missing company is a 404", async () => {
    const { updateContact, problemMessage, refusal } = await fixtureClient();
    const clash = await refusal(updateContact("con-2", { email: "rajesh.khanna@northwind.co" }));
    expect(clash.status).toBe(409);
    expect(problemMessage(clash)).toBe("A contact with that email already exists");
    await expect(updateContact("con-2", { email: "Rajesh.Khanna@northwind.co" })).resolves.toMatchObject({
      email: "Rajesh.Khanna@northwind.co",
    });
    const missing = await refusal(updateContact("con-2", { company_id: "comp-nowhere" }));
    expect(missing.status).toBe(404);
    expect(problemMessage(missing)).toBe("That company is not part of this organization");
  });

  it("a converted lead cannot be given an owner or a status, but its name and notes change", async () => {
    const { updateLead, problemMessage, refusal } = await fixtureClient();
    const error = await refusal(updateLead("lead-5", { owner_id: "own-2" }));
    expect(error.status).toBe(409);
    expect(problemMessage(error)).toBe("A converted lead cannot be reassigned or have its status changed");
    await expect(updateLead("lead-5", { full_name: "P. Nair", notes: "Won." })).resolves.toMatchObject({
      full_name: "P. Nair",
      status: "converted",
    });
    expect((await refusal(updateLead("lead-nowhere", { notes: "x" }))).status).toBe(404);
  });

  it("a closed deal, and a move to the stage a deal is already in, is a 409 in the server's words", async () => {
    const { moveDealStage, problemMessage, refusal } = await fixtureClient();
    const closed = await refusal(moveDealStage("deal-5", { stage: "lead" }));
    expect(closed.status).toBe(409);
    expect(problemMessage(closed)).toBe("A deal in 'closed_won' cannot move to 'lead'");
    const same = await refusal(moveDealStage("deal-4", { stage: "lead" }));
    expect(same.status).toBe(409);
    expect(problemMessage(same)).toBe("A deal in 'lead' cannot move to 'lead'");
    await expect(moveDealStage("deal-4", { stage: "qualified", probability: 40 })).resolves.toMatchObject({
      stage: "qualified",
      probability: 40,
    });
  });

  it("a phone without a country code is a 422 naming the field, and the number is saved masked otherwise", async () => {
    const { setContactPhone, refusal } = await fixtureClient();
    const bad = await refusal(setContactPhone("con-1", { raw_phone: "98765 43210" }));
    expect(bad.status).toBe(422);
    expect(bad.problem?.errors?.map((e) => e.field)).toEqual(["raw_phone"]);
    const saved = await setContactPhone("con-1", { raw_phone: "+91 98765 43210" });
    expect(saved.phone).not.toContain("98765");
    expect((await refusal(setContactPhone("con-nowhere", { raw_phone: "+919876543210" }))).status).toBe(404);
  });
});
