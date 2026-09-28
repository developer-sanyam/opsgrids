/**
 * Create or edit one node of a warehouse's location tree. `kind` and the three capacity fields
 * are disabled in the edit dialog unless the caller holds `operations.inventory.layout.publish`;
 * create leaves them open, since a brand-new location has no stock history for that guard to
 * protect.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PermissionProvider } from "@opsgrids/client";
import type { LocationOut } from "@/api/types";
import { LocationFormDialog } from "./LocationFormDialog";

const createLocation = vi.fn();
const updateLocation = vi.fn();

vi.mock("@/api/client", () => ({
  createLocation: (...args: unknown[]) => createLocation(...args),
  updateLocation: (...args: unknown[]) => updateLocation(...args),
}));

const BIN: LocationOut = {
  id: "loc-1",
  warehouse_id: "wh-1",
  parent_id: null,
  kind: "bin",
  code: "A1-R1-S1-B1",
  name: "Bin A1-R1-S1-B1",
  is_default: false,
  barcode: "LOC-A1R1S1B1",
  pick_sequence: 1,
  max_weight_grams: 25000,
  max_volume_milli: 40000,
  storage_type: "pallet",
};

function renderCreateDialog() {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(
    <LocationFormDialog
      warehouseId="wh-1"
      location={null}
      parentOptions={[]}
      onClose={onClose}
      onSaved={onSaved}
    />,
  );
  return { onClose, onSaved };
}

function renderEditDialog(grants: string[] = []) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(
    <PermissionProvider grants={grants} entitlements={[]}>
      <LocationFormDialog
        warehouseId="wh-1"
        location={BIN}
        parentOptions={[]}
        onClose={onClose}
        onSaved={onSaved}
      />
    </PermissionProvider>,
  );
  return { onClose, onSaved };
}

describe("LocationFormDialog — create", () => {
  beforeEach(() => {
    createLocation.mockReset();
    updateLocation.mockReset();
  });

  it("holds Create until kind, code and name are set", async () => {
    const user = userEvent.setup();
    renderCreateDialog();

    await user.click(screen.getByRole("button", { name: "Create location" }));

    expect(await screen.findByText("Choose a kind.")).toBeDefined();
    expect(createLocation).not.toHaveBeenCalled();
  });

  it("creates a location from the entered fields, omitting unset optional ones", async () => {
    const user = userEvent.setup();
    createLocation.mockResolvedValue({ ...BIN, id: "loc-new" });
    const { onSaved } = renderCreateDialog();

    await user.selectOptions(screen.getByLabelText("Kind"), "zone");
    await user.type(screen.getByLabelText("Code"), "ZONE-B");
    await user.type(screen.getByLabelText("Name"), "Zone B");
    await user.click(screen.getByRole("button", { name: "Create location" }));

    await waitFor(() => expect(createLocation).toHaveBeenCalledTimes(1));
    expect(createLocation).toHaveBeenCalledWith(
      "wh-1",
      expect.objectContaining({ kind: "zone", code: "ZONE-B", name: "Zone B" }),
    );
    const body = createLocation.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(body).not.toHaveProperty("barcode");
    expect(body).not.toHaveProperty("parent_id");
    expect(onSaved).toHaveBeenCalledWith({ ...BIN, id: "loc-new" });
  });

  it("sends the barcode and pick sequence when entered", async () => {
    const user = userEvent.setup();
    createLocation.mockResolvedValue(BIN);
    renderCreateDialog();

    await user.selectOptions(screen.getByLabelText("Kind"), "bin");
    await user.type(screen.getByLabelText("Code"), "B1");
    await user.type(screen.getByLabelText("Name"), "Bin 1");
    await user.type(screen.getByLabelText("Barcode", { exact: false, selector: "input" }), "LOC-B1");
    await user.type(screen.getByLabelText("Pick sequence", { exact: false, selector: "input" }), "3");
    await user.click(screen.getByRole("button", { name: "Create location" }));

    await waitFor(() => expect(createLocation).toHaveBeenCalledTimes(1));
    const body = createLocation.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(body.barcode).toBe("LOC-B1");
    expect(body.pick_sequence).toBe(3);
  });
});

describe("LocationFormDialog — edit", () => {
  beforeEach(() => {
    createLocation.mockReset();
    updateLocation.mockReset();
  });

  it("disables kind and capacity without layout-publish", () => {
    renderEditDialog(["operations.inventory.location.write:ORG"]);

    expect(screen.getByLabelText("Kind")).toHaveProperty("disabled", true);
    expect(screen.getByLabelText("Max weight (grams)", { exact: false, selector: "input" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByLabelText("Max volume (millilitres)", { exact: false, selector: "input" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByLabelText("Storage type", { exact: false, selector: "input" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("leaves code, name, barcode and pick sequence editable regardless", () => {
    renderEditDialog([]);

    expect(screen.getByLabelText("Code")).toHaveProperty("disabled", false);
    expect(screen.getByLabelText("Barcode", { exact: false, selector: "input" })).toHaveProperty("disabled", false);
  });

  it("enables kind and capacity with layout-publish, and sends only what changed", async () => {
    const user = userEvent.setup();
    updateLocation.mockResolvedValue({ ...BIN, name: "Bin 1 renamed" });
    const { onSaved } = renderEditDialog(["operations.inventory.layout.publish:ORG"]);

    expect(screen.getByLabelText("Kind")).toHaveProperty("disabled", false);

    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Bin 1 renamed");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateLocation).toHaveBeenCalledTimes(1));
    const patch = updateLocation.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(patch.name).toBe("Bin 1 renamed");
    // Kind and capacity were left untouched, so they never entered the patch — even though this
    // caller could have changed them.
    expect(patch).not.toHaveProperty("kind");
    expect(patch).not.toHaveProperty("max_weight_grams");
    expect(onSaved).toHaveBeenCalledWith({ ...BIN, name: "Bin 1 renamed" });
  });
});
