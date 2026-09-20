import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as api from "./api";
import type { ZoneOut, StoreOut } from "./types";
import { ZoneFormDialog } from "./ZoneFormDialog";

/**
 * Every state the zone form has, including the two that are easy to leave unbuilt: the
 * visibly-disabled `SLOTTED` mode, and a failed save that keeps what the merchant typed.
 */

const STORE: StoreOut = {
  id: "store-test",
  slug: "greenleaf",
  status: "published",
  currency: "INR",
  timezone: "Asia/Kolkata",
  custom_domain: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

const CREATED: ZoneOut = {
  id: "zone-new",
  code: "PUNE",
  name: "Pune",
  mode: "PROMISED",
  promise_kind: "DURATION",
  promise_minutes: 45,
  promise_offset_days: null,
  promise_cutoff_time: null,
  delivery_fee_minor: 2950,
  free_delivery_above_minor: null,
  min_order_minor: 0,
  is_active: true,
  buyer_note: null,
  postal_code_count: 0,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

function open(onSaved = vi.fn()) {
  render(
    <ZoneFormDialog
      open
      zone={null}
      store={STORE}
      onClose={vi.fn()}
      onSaved={onSaved}
    />,
  );
  return { user: userEvent.setup(), onSaved };
}

describe("ZoneFormDialog", () => {
  it("shows SLOTTED disabled with its reason rather than hiding it", () => {
    open();
    const slotted = screen.getByRole("radio", { name: /Slot booking/ });
    expect(slotted).toBeInstanceOf(HTMLInputElement);
    expect((slotted as HTMLInputElement).disabled).toBe(true);
    // The reason is on screen, not in a tooltip: a merchant who can't see it opens a ticket.
    expect(screen.getByText(/Slot booking arrives with the cart/)).toBeTruthy();
    expect(screen.getByText("Not available yet")).toBeTruthy();
  });

  it("refuses to submit an incomplete form and names each rule", async () => {
    const spy = vi.spyOn(api, "createZone");
    const { user } = open();

    await user.click(screen.getByRole("button", { name: "Create zone" }));

    expect(await screen.findByText("Give the zone a name buyers would recognise.")).toBeTruthy();
    expect(screen.getByText("A zone needs a code — it is what the CSV import refers to.")).toBeTruthy();
    expect(screen.getByText("How many minutes? A whole number, like 30.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("sends money as integer minor units, never as a float", async () => {
    const spy = vi.spyOn(api, "createZone").mockResolvedValue(CREATED);
    const { user } = open();

    await user.type(screen.getByLabelText("Zone name"), "Pune");
    await user.type(screen.getByLabelText("Zone code"), "pune");
    await user.type(screen.getByLabelText("Minutes"), "45");
    await user.type(screen.getByLabelText(/Delivery fee/), "29.50");
    await user.click(screen.getByRole("button", { name: "Create zone" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const sent = spy.mock.calls[0]?.[0];
    expect(sent?.delivery_fee_minor).toBe(2950);
    expect(sent?.code).toBe("PUNE");
    // Fields that do not apply to the chosen mode go as null, never as a stale value.
    expect(sent?.promise_offset_days).toBeNull();
    // An empty money box is 0, not null and not omitted: `min_order_minor` is a non-nullable
    // integer, and on a PATCH an omitted field would silently keep the old amount.
    expect(sent?.min_order_minor).toBe(0);
  });

  it("keeps everything the merchant typed when the server refuses", async () => {
    vi.spyOn(api, "createZone").mockRejectedValue(
      new ApiError(409, "A zone with code PUNE already exists.", null),
    );
    const { user } = open();

    await user.type(screen.getByLabelText("Zone name"), "Pune");
    await user.type(screen.getByLabelText("Zone code"), "PUNE");
    await user.type(screen.getByLabelText("Minutes"), "45");
    await user.click(screen.getByRole("button", { name: "Create zone" }));

    expect(await screen.findByText("A zone with code PUNE already exists.")).toBeTruthy();
    expect((screen.getByLabelText("Zone name") as HTMLInputElement).value).toBe("Pune");
    expect((screen.getByLabelText("Minutes") as HTMLInputElement).value).toBe("45");
  });

  it("swaps the promise fields when the promise kind changes, and names the store's timezone", async () => {
    const { user } = open();

    await user.click(screen.getByRole("radio", { name: /By a date/ }));

    expect(screen.getByLabelText("Days after the order")).toBeTruthy();
    expect(screen.queryByLabelText("Minutes")).toBeNull();
    expect(screen.getByText(/Asia\/Kolkata/)).toBeTruthy();
  });

  it("says the fee fields are display values, because nothing charges them", () => {
    open();
    expect(screen.getByText("These are display values")).toBeTruthy();
    expect(screen.getByText(/Nothing charges them/)).toBeTruthy();
  });
});
