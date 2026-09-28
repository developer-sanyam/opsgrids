/**
 * The warehouse location tree — F1's depth-first, indented list (not a drag-drop editor).
 * Loading, error, empty and populated all get a screen; write actions are gated on
 * `operations.inventory.location.write`.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type { LocationOut, WarehouseOut } from "@/api/types";
import { WarehouseLocationsPage } from "./WarehouseLocations";

const getWarehouseById = vi.fn();
const listLocations = vi.fn();
const createLocation = vi.fn();
const updateLocation = vi.fn();

vi.mock("@/api/client", () => ({
  getWarehouseById: (...args: unknown[]) => getWarehouseById(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
  createLocation: (...args: unknown[]) => createLocation(...args),
  updateLocation: (...args: unknown[]) => updateLocation(...args),
}));

const USER: MeOut = {
  user_id: "user-1",
  email: "ops@example.com",
  full_name: "Asha Rao",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: [],
  amr: [],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const WAREHOUSE: WarehouseOut = {
  id: "wh-1",
  code: "WH-1",
  name: "Bhiwandi DC",
  location: "Bhiwandi, Maharashtra",
};

const ZONE: LocationOut = {
  id: "loc-zone",
  warehouse_id: "wh-1",
  parent_id: null,
  kind: "zone",
  code: "ZONE-A",
  name: "Zone A",
  is_default: false,
  barcode: null,
  pick_sequence: 1,
  max_weight_grams: null,
  max_volume_milli: null,
  storage_type: null,
};

const BIN: LocationOut = {
  id: "loc-bin",
  warehouse_id: "wh-1",
  parent_id: "loc-zone",
  kind: "bin",
  code: "ZONE-A-B1",
  name: "Bin 1",
  is_default: false,
  barcode: "LOC-ZAB1",
  pick_sequence: 1,
  max_weight_grams: 20000,
  max_volume_milli: 30000,
  storage_type: "pallet",
};

function renderPage(grants: string[] = []) {
  const router = createMemoryRouter(
    [
      { path: "/warehouses/:id/locations", element: <WarehouseLocationsPage /> },
      { path: "/warehouses", element: <h1>Warehouses</h1> },
    ],
    { initialEntries: ["/warehouses/wh-1/locations"] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  return router;
}

describe("WarehouseLocationsPage", () => {
  beforeEach(() => {
    getWarehouseById.mockReset();
    listLocations.mockReset();
    createLocation.mockReset();
    updateLocation.mockReset();
  });

  it("shows the empty state with no way to add a location without write permission", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([]);
    renderPage([]);

    expect(await screen.findByText("No locations yet")).toBeDefined();
    expect(screen.queryByRole("button", { name: "New location" })).toBeNull();
  });

  it("shows an action to add the first location with write permission", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([]);
    renderPage(["operations.inventory.location.write:ORG"]);

    await screen.findByText("No locations yet");
    expect(screen.getAllByRole("button", { name: "New location" }).length).toBeGreaterThan(0);
  });

  it("renders the tree depth-first, the bin listed under its zone", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([ZONE, BIN]);
    renderPage([]);

    const table = await screen.findByRole("table", { name: /Locations in Bhiwandi DC/ });
    const rows = within(table).getAllByRole("row");
    // Header row, then Zone A, then Bin 1 nested under it.
    const [, zoneRow, binRow] = rows;
    if (!zoneRow || !binRow) throw new Error("expected a zone row and a bin row");
    expect(within(zoneRow).getByText("ZONE-A")).toBeDefined();
    expect(within(binRow).getByText("ZONE-A-B1")).toBeDefined();
    expect(within(binRow).getByText("LOC-ZAB1")).toBeDefined();
  });

  it("shows the load error with a retry", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockRejectedValue(new Error("network down"));
    renderPage([]);

    expect(await screen.findByText("Couldn't load locations")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("opens the create dialog from the empty state and creates a top-level location", async () => {
    const user = userEvent.setup();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([]);
    createLocation.mockResolvedValue(ZONE);
    renderPage(["operations.inventory.location.write:ORG"]);

    await screen.findByText("No locations yet");
    const [addButton] = screen.getAllByRole("button", { name: "New location" });
    if (!addButton) throw new Error("expected a New location button");
    await user.click(addButton);

    await screen.findByRole("heading", { name: "New location" });
    await user.selectOptions(screen.getByLabelText("Kind"), "zone");
    await user.type(screen.getByLabelText("Code"), "ZONE-A");
    await user.type(screen.getByLabelText("Name"), "Zone A");
    await user.click(screen.getByRole("button", { name: "Create location" }));

    await waitFor(() => expect(createLocation).toHaveBeenCalledWith("wh-1", expect.objectContaining({ code: "ZONE-A" })));
  });
});
