/**
 * The warehouse 3D view (Fulfilment F9): loading, error, empty and populated for the real
 * `listLocations` data, the WebGL fallback chain — the one thing beyond data state this screen has
 * to get right, since a browser without WebGL must never be handed a canvas — and the floor
 * selector: a floor with a published layout is drawn from its real placements, a floor without one
 * keeps the auto-arranged layout and its banner. The actual `@react-three/fiber` scene is mocked
 * out: nothing here can meaningfully assert on WebGL render output, only on the data it was given
 * and on which branch of the fallback chain the page chose.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LocationOut, PublishedLayoutOut, WarehouseFloorOut, WarehouseOut } from "@/api/types";
import { renderRoutes } from "@/test-utils/renderRoutes";
import { stockRow } from "@/test-utils/tracking";
import { Warehouse3DPage } from "./Warehouse3D";

const getWarehouseById = vi.fn();
const listLocations = vi.fn();
const listWarehouseFloors = vi.fn();
const getPublishedLayout = vi.fn();
const listStock = vi.fn();
const hasWebGLSupport = vi.fn();

vi.mock("@/api/client", () => ({
  getWarehouseById: (...args: unknown[]) => getWarehouseById(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
  listWarehouseFloors: (...args: unknown[]) => listWarehouseFloors(...args),
  getPublishedLayout: (...args: unknown[]) => getPublishedLayout(...args),
}));

vi.mock("@/api/tracking", () => ({
  listStock: (...args: unknown[]) => listStock(...args),
}));

vi.mock("@/lib/webgl", () => ({
  hasWebGLSupport: () => hasWebGLSupport(),
}));

// Stands in for the real `@react-three/fiber` canvas: this file has no way to assert on WebGL
// output, only on what the page handed the scene and what happens when it fires `onSelect`.
vi.mock("./Warehouse3DScene", () => ({
  Warehouse3DScene: ({
    layout,
    highlightIds,
    onSelect,
  }: {
    layout: { location: { id: string; code: string }; position: number[] }[];
    highlightIds?: ReadonlySet<string>;
    onSelect: (locationId: string) => void;
  }) => (
    <div data-testid="scene-stub" data-highlight={Array.from(highlightIds ?? []).join(",")}>
      {layout.map((node) => (
        <button
          key={node.location.id}
          type="button"
          data-position={JSON.stringify(node.position)}
          onClick={() => onSelect(node.location.id)}
        >
          Select {node.location.code}
        </button>
      ))}
    </div>
  ),
}));

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

const FLOORS: WarehouseFloorOut[] = [
  { id: "floor-1", warehouse_id: "wh-1", floor_number: 1, name: "Ground" },
  { id: "floor-2", warehouse_id: "wh-1", floor_number: 2, name: "Mezzanine" },
];

const PUBLISHED: PublishedLayoutOut = {
  floor_id: "floor-1",
  warehouse_id: "wh-1",
  floor_name: "Ground",
  published: true,
  layout_version_id: "lv-1",
  placements: [
    {
      location_id: "loc-bin",
      code: "ZONE-A-B1",
      name: "Bin 1",
      kind: "bin",
      parent_id: null,
      x_mm: 2000,
      y_mm: 4000,
      z_mm: null,
      width_mm: 1000,
      depth_mm: 1000,
      height_mm: 1000,
    },
  ],
};

function renderPage(at = "/warehouses/wh-1/3d", grants: string[] = []) {
  return renderRoutes(
    [
      { path: "/warehouses/:id/3d", element: <Warehouse3DPage /> },
      { path: "/warehouses/:id/locations", element: <h1>Locations (2D)</h1> },
    ],
    at,
    grants,
  );
}

describe("Warehouse3DPage", () => {
  beforeEach(() => {
    getWarehouseById.mockReset();
    listLocations.mockReset();
    listWarehouseFloors.mockReset().mockResolvedValue(FLOORS);
    getPublishedLayout.mockReset();
    listStock.mockReset().mockResolvedValue({ items: [], next_cursor: null });
    hasWebGLSupport.mockReset().mockReturnValue(true);
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
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
    // The "no stock endpoint exists" claim is gone: the drawer reads the bin's balances, and a
    // caller without the grant is told why there are none rather than shown a stale excuse.
    expect(screen.queryByText("Stock detail not available yet")).toBeNull();
    expect(screen.getByText("You can't see stock levels")).toBeDefined();
  });

  it("reads the bin's balances in the drawer for a caller who may see stock", async () => {
    const user = userEvent.setup();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([ZONE, BIN]);
    listStock.mockResolvedValue({
      items: [stockRow({ location_id: "loc-bin", sku: "FAB-100", on_hand_qty: "40" })],
      next_cursor: null,
    });
    renderPage("/warehouses/wh-1/3d", ["operations.inventory.stock.read:ORG"]);

    await user.click(await screen.findByRole("button", { name: "Select ZONE-A-B1" }));
    expect(await screen.findByText("FAB-100")).toBeDefined();
    expect(listStock).toHaveBeenCalledWith(expect.objectContaining({ location_id: "loc-bin" }));
  });

  it("falls back to the 2D view, without mounting the scene, when WebGL is unavailable", async () => {
    const user = userEvent.setup();
    hasWebGLSupport.mockReturnValue(false);
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([ZONE, BIN]);
    const { where } = renderPage();

    expect(await screen.findByText("This browser can't show the 3D view")).toBeDefined();
    expect(screen.queryByTestId("scene-stub")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Go to 2D / list view" }));
    await waitFor(() => expect(where().pathname).toBe("/warehouses/wh-1/locations"));
  });

  it("links back to the 2D / list view from the page header action", async () => {
    const user = userEvent.setup();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    listLocations.mockResolvedValue([ZONE]);
    const { where } = renderPage();

    await screen.findByTestId("scene-stub");
    await user.click(screen.getByRole("button", { name: "2D / list view" }));
    await waitFor(() => expect(where().pathname).toBe("/warehouses/wh-1/locations"));
  });

  describe("the floor selector", () => {
    const GRANTS = ["operations.warehouse.floor.read:ORG"];

    it("lists the warehouse's floors after the auto-arranged option, which stays the default", async () => {
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      renderPage("/warehouses/wh-1/3d", GRANTS);

      const select = (await screen.findByLabelText("Floor")) as HTMLSelectElement;
      await waitFor(() => expect(select.options.length).toBe(3));
      expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
        "Auto-arranged (all locations)",
        "Floor 1 — Ground",
        "Floor 2 — Mezzanine",
      ]);
      expect(select.value).toBe("");
      expect(await screen.findByText(/Layout is auto-arranged — no floor plan has been configured/)).toBeDefined();
      expect(getPublishedLayout).not.toHaveBeenCalled();
    });

    it("draws a floor's real placements when it has a published layout, with no 'not real' claim", async () => {
      const user = userEvent.setup();
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      getPublishedLayout.mockResolvedValue(PUBLISHED);
      renderPage("/warehouses/wh-1/3d", GRANTS);

      await user.selectOptions(await screen.findByLabelText("Floor"), "floor-1");

      const scene = await screen.findByTestId("scene-stub");
      expect(getPublishedLayout).toHaveBeenCalledWith("floor-1");
      expect(within(scene).getAllByRole("button")).toHaveLength(1);
      const bin = within(scene).getByRole("button", { name: "Select ZONE-A-B1" });
      expect(JSON.parse(bin.getAttribute("data-position") ?? "[]")).toEqual([2.5, 0.5, 4.5]);
      expect(screen.queryByText(/Layout is auto-arranged/)).toBeNull();
      expect(screen.getByText(/from the published floor plan/)).toBeDefined();
    });

    it("keeps the auto-arranged layout and the banner for a floor with no published layout", async () => {
      const user = userEvent.setup();
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      getPublishedLayout.mockResolvedValue({ ...PUBLISHED, published: false, layout_version_id: null, placements: [] });
      renderPage("/warehouses/wh-1/3d", GRANTS);

      await user.selectOptions(await screen.findByLabelText("Floor"), "floor-1");
      expect(await screen.findByText("Layout is auto-arranged — this floor has no published floor plan yet")).toBeDefined();
      expect(await screen.findByRole("button", { name: "Select ZONE-A" })).toBeDefined();
    });

    it("opens on the floor named in the address and marks the locations a search found", async () => {
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      getPublishedLayout.mockResolvedValue(PUBLISHED);
      renderPage("/warehouses/wh-1/3d?floor=floor-1&highlight=loc-bin,loc-other", GRANTS);

      const scene = await screen.findByTestId("scene-stub");
      expect(scene.getAttribute("data-highlight")).toBe("loc-bin,loc-other");
      expect((screen.getByLabelText("Floor") as HTMLSelectElement).value).toBe("floor-1");
    });

    it("marks a search's locations in the auto-arranged view too", async () => {
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      renderPage("/warehouses/wh-1/3d?highlight=loc-bin", GRANTS);
      expect((await screen.findByTestId("scene-stub")).getAttribute("data-highlight")).toBe("loc-bin");
    });

    it("does not claim there is no floor plan when a search sends an unplaced bin here: the warehouse has one, and the bin is not on it", async () => {
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      renderPage("/warehouses/wh-1/3d?highlight=loc-bin&why=unplaced", GRANTS);

      expect(
        await screen.findByText("Layout is auto-arranged — this location isn't on the warehouse's published floor plan"),
      ).toBeDefined();
      expect(screen.queryByText(/no floor plan has been configured/)).toBeNull();
      expect((await screen.findByTestId("scene-stub")).getAttribute("data-highlight")).toBe("loc-bin");
    });

    it("keeps the no-floor-plan wording for a warehouse that really has none", async () => {
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      renderPage("/warehouses/wh-1/3d?highlight=loc-bin", GRANTS);
      expect(await screen.findByText(/Layout is auto-arranged — no floor plan has been configured/)).toBeDefined();
    });

    it("fails on its own when the floor list will not load, and the auto-arranged view keeps working", async () => {
      const user = userEvent.setup();
      listWarehouseFloors.mockRejectedValueOnce(new Error("floors down"));
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      renderPage("/warehouses/wh-1/3d", GRANTS);

      expect(await screen.findByText("Couldn't load the floor list.")).toBeDefined();
      expect(await screen.findByTestId("scene-stub")).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByLabelText("Floor")).toBeDefined();
    });

    it("shows a failed published-layout load as an error with a retry, scoped to the view", async () => {
      const user = userEvent.setup();
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      getPublishedLayout.mockRejectedValueOnce(new Error("layout down"));
      getPublishedLayout.mockResolvedValueOnce(PUBLISHED);
      renderPage("/warehouses/wh-1/3d?floor=floor-1", GRANTS);

      expect(await screen.findByText("Couldn't load this floor's layout")).toBeDefined();
      expect(screen.getByRole("heading", { name: "Bhiwandi DC" })).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByTestId("scene-stub")).toBeDefined();
    });

    it("lists a placement that cannot be drawn under 'Not on the floor plan'", async () => {
      const first = PUBLISHED.placements[0];
      if (!first) throw new Error("fixture has a placement");
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      getPublishedLayout.mockResolvedValue({
        ...PUBLISHED,
        placements: [...PUBLISHED.placements, { ...first, location_id: "bad", code: "BAD-1", width_mm: 0 }],
      });
      renderPage("/warehouses/wh-1/3d?floor=floor-1", GRANTS);
      expect(await screen.findByRole("heading", { name: "Not on the floor plan" })).toBeDefined();
      expect(screen.getByText("BAD-1")).toBeDefined();
    });

    it("is not offered to someone who cannot read floors", async () => {
      getWarehouseById.mockResolvedValue(WAREHOUSE);
      listLocations.mockResolvedValue([ZONE, BIN]);
      renderPage("/warehouses/wh-1/3d", []);
      await screen.findByTestId("scene-stub");
      expect(screen.queryByLabelText("Floor")).toBeNull();
      expect(listWarehouseFloors).not.toHaveBeenCalled();
    });
  });
});
