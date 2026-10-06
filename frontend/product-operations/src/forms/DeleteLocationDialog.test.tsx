/**
 * Delete location: the server refuses with a 409 and the reason (stock held, history kept, default
 * bin, children, home bin). The reason is shown verbatim and the dialog stays open.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LocationOut } from "@/api/types";
import { problem } from "@/test-utils/tracking";
import { DeleteLocationDialog } from "./DeleteLocationDialog";

const deleteLocation = vi.fn();

vi.mock("@/api/tracking", () => ({
  deleteLocation: (...args: unknown[]) => deleteLocation(...args),
}));

const LOCATION: LocationOut = {
  id: "loc-9",
  warehouse_id: "wh-1",
  parent_id: null,
  kind: "bin",
  code: "A-09",
  name: "Bin A-09",
  is_default: false,
  barcode: null,
  pick_sequence: null,
  max_weight_grams: null,
  max_volume_milli: null,
  storage_type: null,
};

function renderDialog() {
  const user = userEvent.setup();
  const onClose = vi.fn();
  const onDeleted = vi.fn();
  render(<DeleteLocationDialog location={LOCATION} onClose={onClose} onDeleted={onDeleted} />);
  return { user, onClose, onDeleted };
}

describe("DeleteLocationDialog", () => {
  beforeEach(() => {
    deleteLocation.mockReset();
  });

  it("says what is being deleted and what can't be", () => {
    renderDialog();
    expect(screen.getByText(/A-09 — Bin A-09 is removed from the warehouse/)).toBeDefined();
  });

  it("deletes with an idempotency key and reports it", async () => {
    deleteLocation.mockResolvedValue(undefined);
    const { user, onDeleted } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Delete location" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(LOCATION));
    expect(deleteLocation).toHaveBeenCalledWith("loc-9", expect.stringMatching(/^[0-9a-f-]{36}$/));
  });

  it.each([
    "A-09 holds or has held stock, so its history is kept; a location with ledger history cannot be deleted",
    "A-09 is its warehouse's default bin, which holds stock for items that have no other place; it cannot be deleted",
    "A-09 still has 2 location(s) inside it; delete or move them first",
    "A-09 is the home bin of 3 inventory item(s); give them another warehouse first",
  ])("shows the server's 409 reason verbatim and stays open: %s", async (reason) => {
    deleteLocation.mockRejectedValue(problem(409, reason, [], "conflict"));
    const { user, onDeleted } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Delete location" }));

    expect(await screen.findByText(reason)).toBeDefined();
    expect(onDeleted).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDefined();
  });

  it("shows a permission refusal the same way", async () => {
    deleteLocation.mockRejectedValue(problem(403, "You don't have permission to delete locations", [], "forbidden"));
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Delete location" }));

    expect(await screen.findByText("You don't have permission to delete locations")).toBeDefined();
  });
});
