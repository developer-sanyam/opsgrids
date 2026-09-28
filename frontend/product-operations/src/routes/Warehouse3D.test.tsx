/**
 * The warehouse 3D view (Fulfilment F9 MVP): loading, error, empty and populated for the real
 * `listLocations` data, plus the WebGL fallback chain — the one thing beyond data state this
 * screen has to get right, since a browser without WebGL must never be handed a canvas. The
 * actual `@react-three/fiber` scene is mocked out: nothing here can meaningfully assert on WebGL
 * render output (the task brief is explicit that it shouldn't try to), only on the data it was
 * given and on which branch of the fallback chain the page chose.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type { LocationOut, WarehouseOut } from "@/api/types";
import { Warehouse3DPage } from "./Warehouse3D";

const getWarehouseById = vi.fn();
const listLocations = vi.fn();
const hasWebGLSupport = vi.fn();

vi.mock("@/api/client", () => ({
  getWarehouseById: (...args: unknown[]) => getWarehouseById(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
}));

vi.mock("@/lib/webgl", () => ({
  hasWebGLSupport: () => hasWebGLSupport(),
}));

// Stands in for the real `@react-three/fiber` canvas: this file has no way to assert on WebGL
// output, only on what the page handed the scene and what happens when it fires `onSelect`.
vi.mock("./Warehouse3DScene", () => ({
  Warehouse3DScene: ({
    layout,
    onSelect,
  }: {
    layout: { location: LocationOut }[];
    onSelect: (location: LocationOut) => void;
  }) => (
    <div data-testid="scene-stub">
      {layout.map((node) => (
        <button key={node.location.id} type="button" onClick={() => onSelect(node.location)}>
          Select {node.location.code}
        </button>
      ))}
    </div>
  ),
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

function renderPage() {
  const router = createMemoryRouter(
    [
      { path: "/warehouses/:id/3d", element: <Warehouse3DPage /> },
      { path: "/warehouses/:id/locations", element: <h1>Locations (2D)</h1> },
    ],
    { initialEntries: ["/warehouses/wh-1/3d"] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <PermissionProvider grants={[]} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  return router;
}

describe("Warehouse3DPage", () => {
  beforeEach(() => {
    getWarehouseById.mockReset();
    listLocations.mockReset();
    hasWebGLSupport.mockReset().mockReturnValue(true);
  });

  it("shows a loading state while the warehouse and its locations are still in flight", () => {
    getWarehouseById.mockReturnValue(new Promise(() => {}));
    listLocations.mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(screen.getByText("Loading…")).toBeDefined();
    expect(screen.queryByTestId("scene-stub")).toBeNull();
  });

  it("shows the empty state and a way back to the 2D view when there are no locations", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText("No locations yet")).toBeDefined();
    expect(screen.queryByTestId("scene-stub")).toBeNull();
    expect(screen.getByRole("button", { name: "Go to 2D / list view" })).toBeDefined();
  });

  it("shows the load error for the locations request, with a retry", async () => {
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockRejectedValue(new Error("network down"));
    renderPage();

    expect(await screen.findByText("Couldn't load locations")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("shows the load error for the warehouse request, with a retry", async () => {
    getWarehouseById.mockRejectedValue(new Error("network down"));
    listLocations.mockResolvedValue([ZONE]);
    renderPage();

    expect(await screen.findByText("Couldn't load this warehouse")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("renders the scene with the computed layout, and opens the detail drawer when a box is selected", async () => {
    const user = userEvent.setup();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([ZONE, BIN]);
    renderPage();

    await screen.findByTestId("scene-stub");
    expect(screen.getByText(/Layout is auto-arranged/)).toBeDefined();
    // Every real location from `listLocations` reached the scene — nothing dropped, nothing invented.
    expect(screen.getByRole("button", { name: "Select ZONE-A" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Select ZONE-A-B1" })).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Select ZONE-A-B1" }));

    await screen.findByRole("dialog", { name: "Bin 1" });
    expect(screen.getByText("LOC-ZAB1")).toBeDefined(); // barcode
    expect(screen.getByText("20,000 g")).toBeDefined();
    expect(screen.getByText("30,000 mL")).toBeDefined();
    expect(screen.getByText("Stock detail not available yet")).toBeDefined();
  });

  it("falls back to the 2D view, without mounting the scene, when WebGL is unavailable", async () => {
    const user = userEvent.setup();
    hasWebGLSupport.mockReturnValue(false);
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([ZONE, BIN]);
    const router = renderPage();

    expect(await screen.findByText("This browser can't show the 3D view")).toBeDefined();
    expect(screen.queryByTestId("scene-stub")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Go to 2D / list view" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/warehouses/wh-1/locations"));
  });

  it("links back to the 2D / list view from the page header action", async () => {
    const user = userEvent.setup();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([ZONE]);
    const router = renderPage();

    await screen.findByTestId("scene-stub");
    await user.click(screen.getByRole("button", { name: "2D / list view" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/warehouses/wh-1/locations"));
  });
});
