import { describe, expect, it } from "vitest";

import { validateRelease, validateReservationForm } from "./reservationForm";
import type { ReservationFormValues } from "./reservationForm";

const NOW = new Date("2026-10-05T09:00:00Z");
const NO_DISCRETE: ReadonlySet<string> = new Set();
const UUID = "5b6f3c1e-4a9d-4e0a-9d51-2f7a1c0b8e11";

function form(overrides: Partial<ReservationFormValues> = {}): ReservationFormValues {
  return {
    inventoryItemId: "item-1",
    uom: "MT",
    locationId: "bin-1",
    quantity: "12.5",
    referenceType: "manual",
    referenceId: "",
    expiresAt: null,
    note: "  hold for Monday ",
    lotId: "",
    rollId: "",
    ...overrides,
  };
}

describe("validateReservationForm", () => {
  it("builds a manual hold with the quantity as a decimal string and no reference id", () => {
    expect(validateReservationForm(form(), NO_DISCRETE, NOW)).toEqual({
      ok: true,
      payload: {
        inventory_item_id: "item-1",
        location_id: "bin-1",
        quantity: "12.5",
        reference_type: "manual",
        note: "hold for Monday",
      },
    });
  });

  it("requires the document id unless the reservation is manual", () => {
    expect(validateReservationForm(form({ referenceType: "order" }), NO_DISCRETE, NOW)).toMatchObject({
      ok: false,
      errors: { referenceId: "Enter the id of the order this is held for." },
    });
    expect(
      validateReservationForm(form({ referenceType: "production_order", referenceId: "nope" }), NO_DISCRETE, NOW),
    ).toMatchObject({ ok: false, errors: { referenceId: expect.any(String) } });
    expect(
      validateReservationForm(form({ referenceType: "order", referenceId: UUID }), NO_DISCRETE, NOW),
    ).toMatchObject({ ok: true, payload: { reference_type: "order", reference_id: UUID } });
  });

  it("refuses an expiry that is not in the future and sends one that is", () => {
    expect(
      validateReservationForm(form({ expiresAt: "2026-10-05T08:00:00.000Z" }), NO_DISCRETE, NOW),
    ).toMatchObject({ ok: false, errors: { expiresAt: "The expiry has to be in the future." } });
    expect(
      validateReservationForm(form({ expiresAt: "2026-10-06T08:00:00.000Z" }), NO_DISCRETE, NOW),
    ).toMatchObject({ ok: true, payload: { expires_at: "2026-10-06T08:00:00.000Z" } });
  });

  it("refuses a zero, negative or over-precise quantity", () => {
    for (const quantity of ["0", "-3", "1.2345", "abc", ""]) {
      expect(validateReservationForm(form({ quantity }), NO_DISCRETE, NOW).ok, quantity).toBe(false);
    }
  });

  it("needs an item and a bin", () => {
    expect(validateReservationForm(form({ inventoryItemId: "", locationId: "" }), NO_DISCRETE, NOW)).toMatchObject({
      ok: false,
      errors: { item: expect.any(String), location: expect.any(String) },
    });
  });
});

describe("validateRelease", () => {
  it("releases everything with no quantity at all", () => {
    expect(validateRelease("all", "", "30", "MT", NO_DISCRETE)).toEqual({ ok: true, quantity: null });
  });

  it("releases a part, as a decimal string, up to what remains", () => {
    expect(validateRelease("part", "12.250", "30", "MT", NO_DISCRETE)).toEqual({ ok: true, quantity: "12.25" });
    expect(validateRelease("part", "30", "30", "MT", NO_DISCRETE)).toEqual({ ok: true, quantity: "30" });
  });

  it("refuses more than remains, exactly", () => {
    expect(validateRelease("part", "30.001", "30", "MT", NO_DISCRETE)).toEqual({
      ok: false,
      message: "Only 30 MT is still reserved.",
    });
  });

  it("refuses zero, junk and a fraction of a discrete unit", () => {
    expect(validateRelease("part", "0", "30", "MT", NO_DISCRETE).ok).toBe(false);
    expect(validateRelease("part", "x", "30", "MT", NO_DISCRETE).ok).toBe(false);
    expect(validateRelease("part", "2.5", "30", "pcs", new Set(["pcs"]))).toMatchObject({
      ok: false,
      message: "pcs is counted in whole units — no decimals.",
    });
  });
});
