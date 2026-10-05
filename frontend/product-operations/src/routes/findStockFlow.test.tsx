/**
 * The storekeeper's whole journey, across the real screens: pick an item on the Stock page, ask
 * where it is, follow "Show on floor" and land on the floor plan with exactly those bins marked and
 * announced; or follow "Show on map" and land on the warehouse list with the site selected. Only the
 * network and the WebGL canvas are stand-ins.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LocateResultOut } from "@/api/trackingTypes";
import type { PublishedLayoutOut } from "@/api/types";
import { renderRoutes } from "@/test-utils/renderRoutes";
import { item, stockRow } from "@/test-utils/tracking";
import { FloorViewPage } from "./FloorView";
import { InventoryStockPage } from "./InventoryStock";
import { WarehousesMapPage } from "./WarehousesMap";

const listStock = vi.fn();
const locateStock = vi.fn();
const getInventoryItem = vi.fn();
const getPublishedLayout = vi.fn();
const getWarehousesMap = vi.fn();

vi.mock("@/api/tracking", () => ({
  listStock: (...args: unknown[]) => listStock(...args),
  locateStock: (...args: unknown[]) => locateStock(...args),
  listLots: () => Promise.resolve({ items: [], next_cursor: null }),
  listRolls: () => Promise.resolve({ items: [], next_cursor: null }),
}));
vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  getInventoryItem: (...args: unknown[]) => getInventoryItem(...args),
  getPublishedLayout: (...args: unknown[]) => getPublishedLayout(...args),
  getWarehousesMap: (...args: unknown[]) => getWarehousesMap(...args),
  getWarehouseById: () => Promise.resolve({ id: "wh-1", code: "BHW", name: "Bhiwandi DC", location: "" }),
  listWarehouseFloors: () => Promise.resolve([]),
  listWarehouses: () => Promise.resolve({ items: [], total: 0, page: 1, size: 200, pages: 0 }),
  listLocations: () => Promise.resolve([]),
  searchInventory: () => Promise.resolve([]),
  updateWarehouse: () => Promise.reject(new Error("not used")),
  createWarehouse: () => Promise.reject(new Error("not used")),
}));
vi.mock("@/lib/webgl", () => ({ hasWebGLSupport: () => true }));
vi.mock("@/routes/Warehouse3DScene", () => ({ Warehouse3DScene: () => <div data-testid="scene-stub" /> }));

const GRANTS = [
  "operations.inventory.stock.read:ORG",
  "operations.warehouse.layout.read:ORG",
  "operations.warehouse.floor.read:ORG",
  "operations.inventory.warehouse.read:ORG",
];

const PLACEMENT = {
  layout_version_id: "lv-1",
  x_mm: 0,
  y_mm: 0,
  z_mm: null,
  width_mm: 800,
  depth_mm: 600,
  height_mm: null,
  source: "own" as const,
  ancestor_location_id: null,
};

function located(overrides: Partial<LocateResultOut>): LocateResultOut {
  return {
    warehouse_id: "wh-1",
    warehouse_name: "Bhiwandi DC",
    floor_id: "floor-1",
    floor_name: "Ground",
    location_id: "bin-a",
    code: "A-01",
    name: "Bin A-01",
    kind: "bin",
    path: "Rack 1 / A-01",
    quantity: "40",
    status: "AVAILABLE",
    placement: PLACEMENT,
    placement_reason: null,
    ...overrides,
  };
}

const LAYOUT: PublishedLayoutOut = {
  floor_id: "floor-1",
  warehouse_id: "wh-1",
  floor_name: "Ground",
  published: true,
  layout_version_id: "lv-1",
  placements: [
    { location_id: "rack", code: "RACK-1", name: "Rack 1", kind: "rack", parent_id: null, x_mm: 0, y_mm: 0, z_mm: null, width_mm: 6000, depth_mm: 1200, height_mm: null },
    { location_id: "bin-a", code: "A-01", name: "Bin A-01", kind: "bin", parent_id: "rack", x_mm: 200, y_mm: 200, z_mm: null, width_mm: 1000, depth_mm: 800, height_mm: null },
    { location_id: "bin-b", code: "A-02", name: "Bin A-02", kind: "bin", parent_id: "rack", x_mm: 1500, y_mm: 200, z_mm: null, width_mm: 1000, depth_mm: 800, height_mm: null },
    { location_id: "bin-c", code: "A-03", name: "Bin A-03", kind: "bin", parent_id: "rack", x_mm: 2800, y_mm: 200, z_mm: null, width_mm: 1000, depth_mm: 800, height_mm: null },
  ],
};

function renderJourney() {
  return renderRoutes(
    [
      { path: "/inventory/stock", element: <InventoryStockPage /> },
      { path: "/warehouses/:id/floors/:floorId/view", element: <FloorViewPage /> },
      { path: "/warehouses/map", element: <WarehousesMapPage /> },
    ],
    "/inventory/stock?item=item-1",
    GRANTS,
  );
}

describe("search to highlight", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_GOOGLE_MAPS_API_KEY", "");
    listStock.mockReset().mockResolvedValue({ items: [stockRow({ location_id: "bin-a", sku: "FAB-100", on_hand_qty: "40" })], next_cursor: null });
    getInventoryItem.mockReset().mockResolvedValue(item({ id: "item-1", sku: "FAB-100", uom: "m" }));
    getPublishedLayout.mockReset().mockResolvedValue(LAYOUT);
    getWarehousesMap.mockReset().mockResolvedValue({
      sites: [{ id: "wh-1", code: "BHW", name: "Bhiwandi DC", location: "Bhiwandi", lat_micro: 19_300_000, lng_micro: 73_060_000, floor_count: 1, stocked_location_count: 2 }],
      without_coordinates: [],
    });
    locateStock.mockReset().mockResolvedValue({
      results: [
        located({}),
        located({ location_id: "bin-b", code: "A-02", name: "Bin A-02", quantity: "12" }),
        located({ location_id: "bin-z", code: "Z-99", name: "Bin Z-99", placement: null, placement_reason: "unplaced" }),
      ],
    });
    return () => vi.unstubAllEnvs();
  });

  it("goes from a search result to the floor plan with exactly the found bins marked, and says so", async () => {
    const user = userEvent.setup();
    const { where } = renderJourney();

    await user.click(await screen.findByRole("button", { name: "Find where it is" }));
    const region = await screen.findByRole("region", { name: "Stock in Bhiwandi DC" });
    expect(within(region).getByText("Not on floor plan")).toBeDefined();

    await user.click(within(region).getByRole("link", { name: "Show all 2 on floor" }));

    await waitFor(() => expect(where().pathname).toBe("/warehouses/wh-1/floors/floor-1/view"));
    expect(where().search).toBe("?highlight=bin-a,bin-b");
    expect(await screen.findByRole("group", { name: "Floor plan of Ground" })).toBeDefined();

    // Marked in the picture, in the control's own name, and in words.
    expect(screen.getByRole("button", { name: "A-01, Bin, found by your search" })).toBeDefined();
    expect(screen.getByRole("button", { name: "A-02, Bin, found by your search" })).toBeDefined();
    expect(screen.getByRole("button", { name: "A-03, Bin" })).toBeDefined(); // not searched for
    expect(document.querySelectorAll("[data-highlighted='true']")).toHaveLength(2);
    expect(document.querySelectorAll("path[data-pin='true']")).toHaveLength(2);
    expect(
      screen.getAllByRole("status").some((s) => s.textContent === "Highlighting 2 locations on this floor: A-01, A-02."),
    ).toBe(true);
  });

  it("highlights one bin from its own 'Show on floor', and opens its stock from the plan", async () => {
    const user = userEvent.setup();
    const { where } = renderJourney();
    await user.click(await screen.findByRole("button", { name: "Find where it is" }));
    await user.click(await screen.findByRole("link", { name: "Show on floor for A-02" }));

    await waitFor(() => expect(where().search).toBe("?highlight=bin-b"));
    expect(await screen.findByRole("button", { name: "A-02, Bin, found by your search" })).toBeDefined();
    expect(document.querySelectorAll("[data-highlighted='true']")).toHaveLength(1);

    listStock.mockResolvedValue({ items: [stockRow({ location_id: "bin-b", sku: "FAB-100", on_hand_qty: "12" })], next_cursor: null });
    await user.click(screen.getByRole("button", { name: "A-02, Bin, found by your search" }));
    const drawer = await screen.findByRole("dialog", { name: "Bin A-02" });
    expect(await within(drawer).findByText("FAB-100")).toBeDefined();
    expect(listStock).toHaveBeenLastCalledWith(expect.objectContaining({ location_id: "bin-b" }));
  });

  it("goes from a search result to the warehouse on the map page, selected", async () => {
    const user = userEvent.setup();
    const { where } = renderJourney();
    await user.click(await screen.findByRole("button", { name: "Find where it is" }));
    await user.click(await screen.findByRole("link", { name: "Show on map" }));

    await waitFor(() => expect(where().pathname).toBe("/warehouses/map"));
    expect(where().search).toBe("?focus=wh-1");
    const row = (await screen.findByText("Bhiwandi DC")).closest("tr");
    expect(row?.textContent).toContain("Selected");
    // No key in this build: the page is the list, and it says so.
    expect(screen.getByText("The map isn't set up here, so your warehouses are listed instead")).toBeDefined();
  });

  it("says plainly on the floor when what was searched for is not on that floor's plan", async () => {
    getPublishedLayout.mockResolvedValue({ ...LAYOUT, placements: [LAYOUT.placements[0]] });
    const user = userEvent.setup();
    renderJourney();
    await user.click(await screen.findByRole("button", { name: "Find where it is" }));
    await user.click(await screen.findByRole("link", { name: "Show all 2 on floor" }));

    await screen.findByRole("group", { name: "Floor plan of Ground" });
    expect(
      screen.getAllByRole("status").some((s) => s.textContent === "2 of the locations you searched for are not on this floor's plan."),
    ).toBe(true);
    expect(document.querySelector("[data-highlighted='true']")).toBeNull();
  });
});
