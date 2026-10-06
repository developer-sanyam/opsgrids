/**
 * A warehouse's floors (Fulfilment plan §2/F7). Loading, error, empty and populated states;
 * creating a floor is gated on `operations.warehouse.floor.write`.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type { WarehouseFloorOut, WarehouseOut } from "@/api/types";
import { renderRoutes } from "@/test-utils/renderRoutes";
import { WarehouseFloorsPage } from "./WarehouseFloors";

const getWarehouseById = vi.fn();
const listWarehouseFloors = vi.fn();
const createWarehouseFloor = vi.fn();
const updateWarehouseFloor = vi.fn();

vi.mock("@/api/client", () => ({
  getWarehouseById: (...args: unknown[]) => getWarehouseById(...args),
  listWarehouseFloors: (...args: unknown[]) => listWarehouseFloors(...args),
  createWarehouseFloor: (...args: unknown[]) => createWarehouseFloor(...args),
  updateWarehouseFloor: (...args: unknown[]) => updateWarehouseFloor(...args),
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

const GROUND: WarehouseFloorOut = { id: "floor-1", warehouse_id: "wh-1", floor_number: 1, name: "Ground" };

function renderPage(grants: string[] = []) {
  const router = createMemoryRouter(
    [
      { path: "/warehouses/:id/floors", element: <WarehouseFloorsPage /> },
      { path: "/warehouses", element: <h1>Warehouses</h1> },
    ],
    { initialEntries: ["/warehouses/wh-1/floors"] },
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

describe("WarehouseFloorsPage", () => {
  beforeEach(() => {
    getWarehouseById.mockReset();
    listWarehouseFloors.mockReset();
    createWarehouseFloor.mockReset();
    updateWarehouseFloor.mockReset();
  });

  it("shows the empty state with no way to add a floor without write permission", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listWarehouseFloors.mockResolvedValue([]);
    renderPage([]);

    expect(await screen.findByText("No floors yet")).toBeDefined();
    expect(screen.queryByRole("button", { name: "New floor" })).toBeNull();
  });

  it("shows an action to add the first floor with write permission", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listWarehouseFloors.mockResolvedValue([]);
    renderPage(["operations.warehouse.floor.write:ORG"]);

    await screen.findByText("No floors yet");
    expect(screen.getAllByRole("button", { name: "New floor" }).length).toBeGreaterThan(0);
  });

  it("lists floors for the warehouse", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listWarehouseFloors.mockResolvedValue([GROUND]);
    renderPage([]);

    const table = await screen.findByRole("table", { name: /Floors in Bhiwandi DC/ });
    expect(table.textContent).toContain("Ground");
  });

  it("opens a floor's published plan from its row, ahead of the layout editor", async () => {
    const user = userEvent.setup();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listWarehouseFloors.mockResolvedValue([GROUND]);
    const { where } = renderRoutes(
      [
        { path: "/warehouses/:id/floors", element: <WarehouseFloorsPage /> },
        { path: "/warehouses/:id/floors/:floorId/view", element: <h1>Floor view</h1> },
      ],
      "/warehouses/wh-1/floors",
      [],
    );

    await user.click(await screen.findByRole("button", { name: "View floor Ground" }));
    await waitFor(() => expect(where().pathname).toBe("/warehouses/wh-1/floors/floor-1/view"));
  });

  it("shows the load error with a retry", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listWarehouseFloors.mockRejectedValue(new Error("network down"));
    renderPage([]);

    expect(await screen.findByText("Couldn't load floors")).toBeDefined();
  });

  it("creates a floor from the dialog", async () => {
    const user = userEvent.setup();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listWarehouseFloors.mockResolvedValue([]);
    createWarehouseFloor.mockResolvedValue(GROUND);
    renderPage(["operations.warehouse.floor.write:ORG"]);

    await screen.findByText("No floors yet");
    const [newFloorButton] = screen.getAllByRole("button", { name: "New floor" });
    await user.click(newFloorButton!);

    await user.type(screen.getByLabelText("Floor number"), "1");
    await user.type(screen.getByLabelText(/Name/), "Ground");
    await user.click(screen.getByRole("button", { name: "Create floor" }));

    await waitFor(() => {
      expect(createWarehouseFloor).toHaveBeenCalledWith("wh-1", { floor_number: 1, name: "Ground" });
    });
  });
});
