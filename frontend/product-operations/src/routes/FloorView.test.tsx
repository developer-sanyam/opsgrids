/**
 * The floor view: a 2-D plan and a 3-D scene from the same published layout, the search highlight
 * that lands here, and every way a panel can fail without taking the page with it. The 3-D canvas is
 * a stand-in that reports what it was handed — WebGL cannot render in a DOM — so what is asserted is
 * the data (real placements in metres, the highlight set, the banner rules) and the fallbacks.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { LocationOut, PublishedLayoutOut, PublishedPlacementOut, WarehouseFloorOut } from "@/api/types";
import { resetReports } from "@/lib/clientReport";
import { renderRoutes } from "@/test-utils/renderRoutes";
import { stockRow } from "@/test-utils/tracking";
import { FloorViewPage } from "./FloorView";

const getPublishedLayout = vi.fn();
const getWarehouseById = vi.fn();
const listWarehouseFloors = vi.fn();
const listLocations = vi.fn();
const listStock = vi.fn();
const hasWebGLSupport = vi.fn();
const sceneProps = vi.fn();
let sceneThrows = false;

vi.mock("@/api/client", () => ({
  getPublishedLayout: (...args: unknown[]) => getPublishedLayout(...args),
  getWarehouseById: (...args: unknown[]) => getWarehouseById(...args),
  listWarehouseFloors: (...args: unknown[]) => listWarehouseFloors(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
}));
vi.mock("@/api/tracking", () => ({
  listStock: (...args: unknown[]) => listStock(...args),
}));
vi.mock("@/lib/webgl", () => ({ hasWebGLSupport: () => hasWebGLSupport() }));
vi.mock("@/routes/Warehouse3DScene", () => ({
  Warehouse3DScene: (props: {
    layout: { location: { id: string; code: string }; position: number[]; size: number[] }[];
    highlightIds?: ReadonlySet<string>;
    selectedId: string | null;
    onSelect: (id: string) => void;
  }) => {
    if (sceneThrows) throw new Error("three.js blew up");
    sceneProps(props);
    return (
      <div data-testid="scene-stub" data-highlight={Array.from(props.highlightIds ?? []).join(",")}>
        {props.layout.map((node) => (
          <button
            key={node.location.id}
            type="button"
            data-position={JSON.stringify(node.position)}
            data-size={JSON.stringify(node.size)}
            onClick={() => props.onSelect(node.location.id)}
          >
            Select {node.location.code}
          </button>
        ))}
      </div>
    );
  },
}));

const LAYOUT_READ = "operations.warehouse.layout.read:ORG";
const FLOOR_READ = "operations.warehouse.floor.read:ORG";
const LAYOUT_WRITE = "operations.warehouse.layout.write:ORG";
const STOCK_READ = "operations.inventory.stock.read:ORG";
const GRANTS = [LAYOUT_READ, FLOOR_READ, STOCK_READ];

function placement(overrides: Partial<PublishedPlacementOut>): PublishedPlacementOut {
  return {
    location_id: "loc",
    code: "X",
    name: "X",
    kind: "bin",
    parent_id: null,
    x_mm: 0,
    y_mm: 0,
    z_mm: null,
    width_mm: 1000,
    depth_mm: 800,
    height_mm: null,
    ...overrides,
  };
}

const RACK = placement({ location_id: "rack", code: "RACK-1", name: "Rack 1", kind: "rack", width_mm: 6000, depth_mm: 1200 });
const BIN_A = placement({ location_id: "bin-a", code: "A-01", name: "Bin A-01", parent_id: "rack", x_mm: 1000, y_mm: 2000, z_mm: 500, width_mm: 800, depth_mm: 600, height_mm: 1200 });
const BIN_B = placement({ location_id: "bin-b", code: "A-02", name: "Bin A-02", parent_id: "rack", x_mm: 3000 });
const BROKEN = placement({ location_id: "bad", code: "BAD-1", name: "Zero width bin", width_mm: 0 });

function layout(overrides: Partial<PublishedLayoutOut> = {}): PublishedLayoutOut {
  return {
    floor_id: "floor-1",
    warehouse_id: "wh-1",
    floor_name: "Ground floor",
    published: true,
    layout_version_id: "lv-1",
    placements: [RACK, BIN_A, BIN_B],
    ...overrides,
  };
}

const FLOORS: WarehouseFloorOut[] = [
  { id: "floor-1", warehouse_id: "wh-1", floor_number: 1, name: "Ground floor" },
  { id: "floor-2", warehouse_id: "wh-1", floor_number: 2, name: "Mezzanine" },
];

const TREE: LocationOut[] = [
  {
    id: "zone",
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
  },
];

function renderFloor(at = "/warehouses/wh-1/floors/floor-1/view", grants = GRANTS) {
  return renderRoutes(
    [
      { path: "/warehouses/:id/floors/:floorId/view", element: <FloorViewPage /> },
      { path: "/warehouses/:id/floors", element: <h1>Floors list</h1> },
      { path: "/warehouses/:id/floors/:floorId", element: <h1>Layout editor</h1> },
    ],
    at,
    grants,
  );
}

describe("FloorViewPage", () => {
  beforeEach(() => {
    resetReports();
    sceneThrows = false;
    getPublishedLayout.mockReset().mockResolvedValue(layout());
    getWarehouseById.mockReset().mockResolvedValue({ id: "wh-1", code: "BHW", name: "Bhiwandi DC", location: "" });
    listWarehouseFloors.mockReset().mockResolvedValue(FLOORS);
    listLocations.mockReset().mockResolvedValue(TREE);
    listStock.mockReset().mockResolvedValue({ items: [], next_cursor: null });
    hasWebGLSupport.mockReset().mockReturnValue(true);
    sceneProps.mockReset();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  describe("the 2-D plan", () => {
    it("draws the floor's published placements, named for the floor and its warehouse", async () => {
      renderFloor();
      expect(await screen.findByRole("heading", { name: "Ground floor" })).toBeDefined();
      expect(await screen.findByRole("group", { name: "Floor plan of Ground floor" })).toBeDefined();
      expect(screen.getByRole("button", { name: "RACK-1, Rack" })).toBeDefined();
      expect(screen.getByRole("button", { name: "A-01, Bin" })).toBeDefined();
      expect(screen.getByText("Bhiwandi DC")).toBeDefined();
      expect(getPublishedLayout).toHaveBeenCalledWith("floor-1");
      expect(screen.queryByText(/Layout is auto-arranged/)).toBeNull();
    });

    it("is the default view, and needs no WebGL", async () => {
      hasWebGLSupport.mockReturnValue(false);
      renderFloor();
      expect(await screen.findByRole("group", { name: "Floor plan of Ground floor" })).toBeDefined();
      expect(screen.getByRole("tab", { name: "2-D plan" }).getAttribute("aria-selected")).toBe("true");
    });

    it("lists a placement it cannot draw under 'Not on the floor plan' instead of dropping or drawing it", async () => {
      getPublishedLayout.mockResolvedValue(layout({ placements: [RACK, BROKEN] }));
      renderFloor();
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      expect(screen.queryByRole("button", { name: /BAD-1/ })).toBeNull();
      expect(screen.getByRole("heading", { name: "Not on the floor plan" })).toBeDefined();
      expect(screen.getByText("BAD-1")).toBeDefined();
      expect(screen.getByText(/Zero width bin/)).toBeDefined();
    });

    it("says nothing can be drawn when every placement is unusable, and still lists them", async () => {
      getPublishedLayout.mockResolvedValue(layout({ placements: [BROKEN] }));
      renderFloor();
      expect(await screen.findByText("Nothing on this floor's published layout can be drawn")).toBeDefined();
      expect(screen.getByText("BAD-1")).toBeDefined();
    });

    it("explains a floor with no published layout instead of showing a blank plan, and offers the 3-D view", async () => {
      getPublishedLayout.mockResolvedValue(layout({ published: false, layout_version_id: null, placements: [] }));
      const user = userEvent.setup();
      renderFloor();
      expect(await screen.findByText("This floor has no published layout yet")).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Open the 3-D view" }));
      expect(await screen.findByTestId("scene-stub")).toBeDefined();
    });
  });

  describe("the highlight a search sends here", () => {
    it("marks every found location on the plan and says in words which, and how many were not on it", async () => {
      renderFloor("/warehouses/wh-1/floors/floor-1/view?highlight=bin-a,bin-b,ghost");
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      expect(screen.getByRole("button", { name: "A-01, Bin, found by your search" })).toBeDefined();
      expect(screen.getByRole("button", { name: "A-02, Bin, found by your search" })).toBeDefined();
      expect(screen.getByRole("button", { name: "RACK-1, Rack" })).toBeDefined();
      const status = screen
        .getAllByRole("status")
        .map((s) => s.textContent)
        .find((text) => text?.includes("Highlighting"));
      expect(status).toBe(
        "Highlighting 2 locations on this floor: A-01, A-02. 1 of the locations you searched for is not on this floor's plan.",
      );
    });

    it("says so when none of what was searched for is on this floor", async () => {
      renderFloor("/warehouses/wh-1/floors/floor-1/view?highlight=ghost,ghost-2");
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      expect(
        screen.getAllByRole("status").some((s) => s.textContent === "2 of the locations you searched for are not on this floor's plan."),
      ).toBe(true);
      expect(document.querySelector("[data-highlighted='true']")).toBeNull();
    });

    it("has no highlight and no status without the parameter", async () => {
      renderFloor();
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      expect(document.querySelector("[data-highlighted='true']")).toBeNull();
      expect(screen.getAllByRole("status").every((s) => !s.textContent?.includes("Highlighting"))).toBe(true);
    });

    it("passes the same highlight to the 3-D scene", async () => {
      renderFloor("/warehouses/wh-1/floors/floor-1/view?view=3d&highlight=bin-a,bin-b");
      const scene = await screen.findByTestId("scene-stub");
      expect(scene.getAttribute("data-highlight")).toBe("bin-a,bin-b");
    });
  });

  describe("the 3-D view", () => {
    it("is fed the real published placements in metres — plan y on Z, elevation on Y — with no 'auto-arranged' claim", async () => {
      const user = userEvent.setup();
      renderFloor();
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      await user.click(screen.getByRole("tab", { name: "3-D view" }));

      const scene = await screen.findByTestId("scene-stub");
      const binA = within(scene).getByRole("button", { name: "Select A-01" });
      const [x, y, z] = JSON.parse(binA.getAttribute("data-position") ?? "[]") as number[];
      expect(x).toBeCloseTo(1.4); // (1000 + 800/2) mm
      expect(y).toBeCloseTo(1.1); // (500 + 1200/2) mm
      expect(z).toBeCloseTo(2.3); // (2000 + 600/2) mm
      expect(JSON.parse(binA.getAttribute("data-size") ?? "[]")).toEqual([0.8, 1.2, 0.6]);

      expect(screen.queryByText(/Layout is auto-arranged/)).toBeNull();
      expect(screen.getByText(/from the published floor plan/)).toBeDefined();
      expect(listLocations).not.toHaveBeenCalled();
    });

    it("keeps the auto-arranged layout and its banner for a floor with no published layout", async () => {
      getPublishedLayout.mockResolvedValue(layout({ published: false, layout_version_id: null, placements: [] }));
      renderFloor("/warehouses/wh-1/floors/floor-1/view?view=3d");
      expect(await screen.findByText("Layout is auto-arranged — this floor has no published floor plan yet")).toBeDefined();
      expect(await screen.findByRole("button", { name: "Select ZONE-A" })).toBeDefined();
      expect(listLocations).toHaveBeenCalledWith("wh-1");
    });

    it("never draws an unusable placement in the scene", async () => {
      getPublishedLayout.mockResolvedValue(layout({ placements: [RACK, BROKEN] }));
      renderFloor("/warehouses/wh-1/floors/floor-1/view?view=3d");
      const scene = await screen.findByTestId("scene-stub");
      expect(within(scene).queryByRole("button", { name: /BAD-1/ })).toBeNull();
      expect(within(scene).getByRole("button", { name: "Select RACK-1" })).toBeDefined();
    });

    it("keeps the chosen tab in the address", async () => {
      const user = userEvent.setup();
      const { where } = renderFloor();
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      await user.click(screen.getByRole("tab", { name: "3-D view" }));
      expect(where().search).toContain("view=3d");
      await user.click(screen.getByRole("tab", { name: "2-D plan" }));
      expect(where().search).not.toContain("view=3d");
    });

    it("falls back to a message and the 2-D plan when WebGL is unavailable, and reports it once", async () => {
      hasWebGLSupport.mockReturnValue(false);
      const user = userEvent.setup();
      renderFloor("/warehouses/wh-1/floors/floor-1/view?view=3d");
      expect(await screen.findByText("This browser can't show the 3D view")).toBeDefined();
      expect(screen.queryByTestId("scene-stub")).toBeNull();
      expect(console.warn).toHaveBeenCalledWith("[operations] webgl_unavailable (no_webgl)");

      await user.click(screen.getByRole("button", { name: "Show the 2-D plan" }));
      expect(await screen.findByRole("group", { name: "Floor plan of Ground floor" })).toBeDefined();
    });

    it("falls back the same way when the scene throws, and the rest of the page stays", async () => {
      vi.spyOn(console, "error").mockImplementation(() => undefined);
      sceneThrows = true;
      renderFloor("/warehouses/wh-1/floors/floor-1/view?view=3d");
      expect(await screen.findByText("This browser can't show the 3D view")).toBeDefined();
      expect(screen.getByRole("heading", { name: "Ground floor" })).toBeDefined();
      expect(screen.getByRole("tab", { name: "2-D plan" })).toBeDefined();
      expect(console.warn).toHaveBeenCalledWith("[operations] floor_render_failed (render_error)");
    });
  });

  describe("loading, failing and permission", () => {
    it("holds the plan's place with a skeleton, and says so after 8 seconds", async () => {
      vi.useFakeTimers();
      getPublishedLayout.mockReturnValue(new Promise(() => undefined));
      renderFloor();
      await act(async () => {
        await Promise.resolve();
      });
      expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
      await act(async () => {
        vi.advanceTimersByTime(8000);
      });
      expect(screen.getAllByRole("status").some((s) => s.textContent?.includes("taking longer than usual"))).toBe(true);
    });

    it("shows a failed layout load as an error with a retry, and nothing else on the page breaks", async () => {
      const user = userEvent.setup();
      getPublishedLayout.mockRejectedValueOnce(new ApiError(500, "The layout service is down", { detail: "The layout service is down" }));
      renderFloor();
      expect(await screen.findByText("The layout service is down")).toBeDefined();
      expect(screen.getByRole("link", { name: "Back to floors" })).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByRole("group", { name: "Floor plan of Ground floor" })).toBeDefined();
    });

    it("shows a floor that is not there (404) as an error with a way back", async () => {
      getPublishedLayout.mockRejectedValue(new ApiError(404, "That floor is not part of this organization", { detail: "That floor is not part of this organization" }));
      renderFloor();
      expect(await screen.findByText("That floor is not part of this organization")).toBeDefined();
    });

    it("explains a missing grant and does not ask for the layout", async () => {
      renderFloor("/warehouses/wh-1/floors/floor-1/view", [STOCK_READ]);
      expect(await screen.findByText("You don't have access to floor plans")).toBeDefined();
      expect(getPublishedLayout).not.toHaveBeenCalled();
    });

    it("still works when the warehouse name cannot be loaded", async () => {
      getWarehouseById.mockRejectedValue(new Error("down"));
      renderFloor();
      expect(await screen.findByRole("group", { name: "Floor plan of Ground floor" })).toBeDefined();
      expect(screen.getByText("Floor plan")).toBeDefined();
    });

    it("offers Edit layout only to someone who can write layouts", async () => {
      const { unmount } = renderFloor();
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      expect(screen.queryByRole("button", { name: "Edit layout" })).toBeNull();
      unmount();
      renderFloor("/warehouses/wh-1/floors/floor-1/view", [...GRANTS, LAYOUT_WRITE]);
      expect(await screen.findByRole("button", { name: "Edit layout" })).toBeDefined();
    });
  });

  describe("the floor selector", () => {
    it("lists the warehouse's floors and moves to another one, keeping the 3-D tab", async () => {
      const user = userEvent.setup();
      const { where } = renderFloor("/warehouses/wh-1/floors/floor-1/view?view=3d");
      const select = (await screen.findByLabelText("Floor")) as HTMLSelectElement;
      await waitFor(() => expect(select.options.length).toBe(2));
      expect(select.value).toBe("floor-1");
      await user.selectOptions(select, "floor-2");
      await waitFor(() => expect(where().pathname).toBe("/warehouses/wh-1/floors/floor-2/view"));
      expect(where().search).toBe("?view=3d");
    });

    it("fails on its own: says it could not load the floors and keeps the plan", async () => {
      const user = userEvent.setup();
      listWarehouseFloors.mockRejectedValueOnce(new Error("floors down"));
      renderFloor();
      expect(await screen.findByText("Couldn't load the floor list.")).toBeDefined();
      expect(await screen.findByRole("group", { name: "Floor plan of Ground floor" })).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByLabelText("Floor")).toBeDefined();
    });

    it("is not shown to someone who cannot read floors, and never asks for them", async () => {
      renderFloor("/warehouses/wh-1/floors/floor-1/view", [LAYOUT_READ]);
      await screen.findByRole("group", { name: "Floor plan of Ground floor" });
      expect(screen.queryByLabelText("Floor")).toBeNull();
      expect(listWarehouseFloors).not.toHaveBeenCalled();
    });
  });

  describe("the bin drawer", () => {
    it("opens on a clicked location and lists what is stored there, asking by location", async () => {
      const user = userEvent.setup();
      listStock.mockResolvedValue({
        items: [stockRow({ id: "b1", location_id: "bin-a", sku: "FAB-100", on_hand_qty: "82.5", lot_no: "LOT-7" })],
        next_cursor: null,
      });
      renderFloor();
      await user.click(await screen.findByRole("button", { name: "A-01, Bin" }));

      const drawer = await screen.findByRole("dialog", { name: "Bin A-01" });
      expect(await within(drawer).findByText("FAB-100")).toBeDefined();
      expect(within(drawer).getByText(/Lot LOT-7/)).toBeDefined();
      expect(listStock).toHaveBeenCalledWith(expect.objectContaining({ location_id: "bin-a" }));
    });

    it("says nothing is stored in an empty bin", async () => {
      const user = userEvent.setup();
      renderFloor();
      await user.click(await screen.findByRole("button", { name: "A-01, Bin" }));
      expect(await screen.findByText("Nothing stored here")).toBeDefined();
    });

    it("keeps the bin's own details and shows a stock error with a retry when the balances fail", async () => {
      const user = userEvent.setup();
      listStock.mockRejectedValueOnce(new ApiError(503, "Stock is unavailable", { detail: "Stock is unavailable" }));
      listStock.mockResolvedValueOnce({ items: [stockRow({ location_id: "bin-a" })], next_cursor: null });
      renderFloor();
      await user.click(await screen.findByRole("button", { name: "A-01, Bin" }));

      const drawer = await screen.findByRole("dialog", { name: "Bin A-01" });
      expect(await within(drawer).findByText("Couldn't load this location's stock")).toBeDefined();
      expect(within(drawer).getByText("Stock is unavailable")).toBeDefined();
      expect(within(drawer).getByText("Kind")).toBeDefined(); // the bin's own fields are still there
      expect(screen.getByRole("group", { name: "Floor plan of Ground floor" })).toBeDefined(); // and so is the page

      await user.click(within(drawer).getByRole("button", { name: "Try again" }));
      expect(await within(drawer).findByText("FAB-100")).toBeDefined();
    });

    it("tells someone without stock.read why there are no figures, and does not ask for them", async () => {
      const user = userEvent.setup();
      renderFloor("/warehouses/wh-1/floors/floor-1/view", [LAYOUT_READ, FLOOR_READ]);
      await user.click(await screen.findByRole("button", { name: "A-01, Bin" }));
      expect(await screen.findByText("You can't see stock levels")).toBeDefined();
      expect(listStock).not.toHaveBeenCalled();
    });

    it("opens from a click in the 3-D scene as well", async () => {
      const user = userEvent.setup();
      renderFloor("/warehouses/wh-1/floors/floor-1/view?view=3d");
      await user.click(await screen.findByRole("button", { name: "Select A-02" }));
      expect(await screen.findByRole("dialog", { name: "Bin A-02" })).toBeDefined();
    });

    it("says there is more than the drawer shows, and links to the Stock screen", async () => {
      const user = userEvent.setup();
      listStock.mockResolvedValue({ items: [stockRow({ location_id: "bin-a" })], next_cursor: "more" });
      renderFloor();
      await user.click(await screen.findByRole("button", { name: "A-01, Bin" }));
      const link = await screen.findByRole("link", { name: "See all stock in this location" });
      expect(link.getAttribute("href")).toBe("/inventory/stock?location=bin-a");
    });
  });
});
