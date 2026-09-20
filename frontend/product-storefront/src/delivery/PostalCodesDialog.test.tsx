import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as api from "./api";
import { PostalCodesDialog } from "./PostalCodesDialog";
import type { PostalCodeMap } from "./postalCodes";
import type { ZoneOut } from "./types";

/**
 * The promise this file keeps: **nothing is written before the merchant has seen what it would do.**
 *
 * A postal code belongs to exactly one zone per organization, so re-assignment is a decision
 * (ADR-0010 §2). These tests pin that the decision is shown — with the zone each code is leaving —
 * and that a failed save leaves the merchant's list where they typed it.
 */

const CENTRAL: ZoneOut = {
  id: "z-central",
  code: "MUM-CENTRAL",
  name: "Mumbai — central",
  mode: "PROMISED",
  promise_kind: "DURATION",
  promise_minutes: 30,
  promise_offset_days: null,
  promise_cutoff_time: null,
  delivery_fee_minor: 2900,
  free_delivery_above_minor: null,
  min_order_minor: 0,
  is_active: true,
  buyer_note: null,
  postal_code_count: 2,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

const SUBURBS: ZoneOut = { ...CENTRAL, id: "z-suburbs", code: "MUM-SUB", name: "Mumbai — suburbs" };

const MAP: PostalCodeMap = new Map([
  ["400050", { zoneId: "z-central", zoneCode: "MUM-CENTRAL", zoneName: "Mumbai — central" }],
  ["400051", { zoneId: "z-central", zoneCode: "MUM-CENTRAL", zoneName: "Mumbai — central" }],
  ["400072", { zoneId: "z-suburbs", zoneCode: "MUM-SUB", zoneName: "Mumbai — suburbs" }],
]);

afterEach(() => {
  vi.restoreAllMocks();
});

async function openDialog(onSaved = vi.fn()) {
  vi.spyOn(api, "listZonePostalCodes").mockResolvedValue({
    zone_id: "z-central",
    zone_code: "MUM-CENTRAL",
    postal_codes: ["400050", "400051"],
    total: 2,
  });
  vi.spyOn(api, "loadPostalCodeMap").mockResolvedValue(MAP);

  render(
    <PostalCodesDialog
      open
      zone={CENTRAL}
      zones={[CENTRAL, SUBURBS]}
      onClose={vi.fn()}
      onSaved={onSaved}
    />,
  );

  const box = await screen.findByLabelText("Postal codes");
  return { user: userEvent.setup(), box: box as HTMLTextAreaElement, onSaved };
}

describe("PostalCodesDialog", () => {
  it("seeds the box with the zone's current codes, so a PUT is not a surprise deletion", async () => {
    const { box } = await openDialog();
    expect(box.value).toBe("400050\n400051");
  });

  it("names the zone a code would be taken from, before anything is written", async () => {
    const save = vi.spyOn(api, "saveZonePostalCodes");
    const { user, box } = await openDialog();

    await user.clear(box);
    await user.type(box, "400050\n400072\n400090");

    expect(await screen.findByText("Moving out of another zone")).toBeTruthy();
    expect(screen.getByText("Mumbai — suburbs")).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("counts what a save would drop, because the PUT carries the whole set", async () => {
    const { user, box } = await openDialog();

    await user.clear(box);
    await user.type(box, "400050");

    expect(await screen.findByText("Dropped from this zone")).toBeTruthy();
    expect(screen.getByText(/Saving removes them/)).toBeTruthy();
  });

  it("refuses to enable the save when the paste would change nothing", async () => {
    await openDialog();
    expect(
      (screen.getByRole("button", { name: "Nothing to save" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("reports the server's own counters after the write, not its own preview", async () => {
    // `ZonePostalCodesOut` is the zone's resulting set, not a set of counters — the route has none.
    const save = vi.spyOn(api, "saveZonePostalCodes").mockResolvedValue({
      zone_id: "z-central",
      zone_code: "MUM-CENTRAL",
      postal_codes: ["400050", "400072", "400090"],
      total: 3,
    });
    const { user, box } = await openDialog();

    await user.clear(box);
    await user.type(box, "400050\n400072\n400090");
    await user.click(screen.getByRole("button", { name: "Save these postal codes" }));

    await waitFor(() => screen.getByText("Saved"));
    // The server's own number is the headline; the breakdown beside it is labelled as the request.
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("What this save asked for")).toBeTruthy();
    // The raw text went over the wire, not a parsed array: the server owns the parse.
    expect(typeof save.mock.calls[0]?.[1]).toBe("string");
  });

  it("keeps the merchant's list when the save fails", async () => {
    vi.spyOn(api, "saveZonePostalCodes").mockRejectedValue(
      new ApiError(503, "upstream unavailable", null),
    );
    const { user, box } = await openDialog();

    await user.clear(box);
    await user.type(box, "400090");
    await user.click(screen.getByRole("button", { name: "Save these postal codes" }));

    await waitFor(() => screen.getByText("That didn't save"));
    expect((screen.getByLabelText("Postal codes") as HTMLTextAreaElement).value).toBe("400090");
  });
});
