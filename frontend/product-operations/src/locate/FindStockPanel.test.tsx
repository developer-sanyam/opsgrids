/**
 * Find stock: looks only when asked, groups what it finds by warehouse and floor, says in words why
 * a bin is not on a plan, announces its outcome, and keeps every failure inside its own card.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { LocateOut, LocateResultOut } from "@/api/trackingTypes";
import { renderScreen } from "@/test-utils/renderScreen";
import { FindStockPanel } from "./FindStockPanel";

const locateStock = vi.fn();

vi.mock("@/api/tracking", () => ({
  locateStock: (...args: unknown[]) => locateStock(...args),
}));

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

function result(overrides: Partial<LocateResultOut> = {}): LocateResultOut {
  return {
    warehouse_id: "wh-1",
    warehouse_name: "Bhiwandi DC",
    floor_id: "floor-1",
    floor_name: "Ground",
    location_id: "loc-1",
    code: "A-01",
    name: "Bin A-01",
    kind: "bin",
    path: "Zone A / Rack 1 / A-01",
    quantity: "82.5",
    status: "AVAILABLE",
    placement: PLACEMENT,
    placement_reason: null,
    ...overrides,
  };
}

function found(...results: LocateResultOut[]): LocateOut {
  return { results };
}

const READ = "operations.inventory.stock.read:ORG";

function renderPanel(props: Partial<Parameters<typeof FindStockPanel>[0]> = {}) {
  return renderScreen(<FindStockPanel itemId="item-1" subject="FAB-100" uom="m" {...props} />, [READ]);
}

describe("FindStockPanel", () => {
  beforeEach(() => {
    locateStock.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks nothing until it is asked, and says what it will do", () => {
    renderPanel();
    expect(locateStock).not.toHaveBeenCalled();
    expect(screen.getByText("Which bins hold FAB-100 right now, and where they are.")).toBeDefined();
    expect((screen.getByRole("button", { name: "Find where it is" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("waits for an item: the button is disabled and the hint says to choose one", () => {
    renderPanel({ itemId: "", subject: undefined });
    expect((screen.getByRole("button", { name: "Find where it is" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Choose an item above to see which bins hold it.")).toBeDefined();
  });

  it("asks for the item, narrowed to the lot or roll it was given", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result()));
    renderPanel({ lotId: "lot-9", rollId: "roll-3" });
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    expect(locateStock).toHaveBeenCalledWith({
      inventory_item_id: "item-1",
      lot_id: "lot-9",
      roll_id: "roll-3",
    });
  });

  it("groups results by warehouse then floor, with quantity, unit and status in words", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(
      found(
        result(),
        result({ location_id: "loc-2", code: "B-02", name: "Bin B-02", quantity: "10", status: "QUALITY_HOLD" }),
        result({ location_id: "loc-3", code: "M-01", name: "Bin M-01", floor_id: "floor-2", floor_name: "Mezzanine" }),
        result({ warehouse_id: "wh-2", warehouse_name: "Pune Hub", floor_id: null, floor_name: null, location_id: "loc-4", code: "P-1", placement: null, placement_reason: "unplaced" }),
      ),
    );
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));

    const bhiwandi = await screen.findByRole("region", { name: "Stock in Bhiwandi DC" });
    expect(within(bhiwandi).getByRole("heading", { level: 4, name: "Ground" })).toBeDefined();
    expect(within(bhiwandi).getByRole("heading", { level: 4, name: "Mezzanine" })).toBeDefined();
    expect(within(bhiwandi).getAllByText("82.5").length).toBeGreaterThan(0);
    expect(within(bhiwandi).getAllByText(/m$/).length).toBeGreaterThan(0);
    expect(within(bhiwandi).getByText(/Quality hold/)).toBeDefined();
    expect(within(bhiwandi).getAllByText("Zone A / Rack 1 / A-01").length).toBeGreaterThan(0);

    const pune = screen.getByRole("region", { name: "Stock in Pune Hub" });
    expect(within(pune).getByRole("heading", { level: 4, name: "No floor assigned" })).toBeDefined();
  });

  it("announces how many places hold it, in a polite live region", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result(), result({ location_id: "loc-2", code: "B-02" })));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    await screen.findByRole("region", { name: "Stock in Bhiwandi DC" });
    const region = screen.getAllByRole("status").find((s) => s.textContent?.startsWith("Found in"));
    expect(region?.textContent).toBe("Found in 2 locations across 1 warehouse.");
    expect(region?.getAttribute("aria-live")).toBe("polite");
  });

  it("links 'Show on floor' to the floor view with that bin highlighted, and the warehouse to the map", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result()));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));

    const onFloor = await screen.findByRole("link", { name: "Show on floor for A-01" });
    expect(onFloor.getAttribute("href")).toBe("/warehouses/wh-1/floors/floor-1/view?highlight=loc-1");
    expect(screen.getByRole("link", { name: "Show on map" }).getAttribute("href")).toBe("/warehouses/map?focus=wh-1");
  });

  it("offers one link that highlights every placed bin on a floor, with several ids", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result(), result({ location_id: "loc-2", code: "B-02" })));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    const all = await screen.findByRole("link", { name: "Show all 2 on floor" });
    expect(all.getAttribute("href")).toBe("/warehouses/wh-1/floors/floor-1/view?highlight=loc-1,loc-2");
  });

  it("points a bin that is only placed through its rack at the rack, and says so", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(
      found(result({ placement: { ...PLACEMENT, source: "ancestor", ancestor_location_id: "rack-9" } })),
    );
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    const link = await screen.findByRole("link", { name: "Show on floor for A-01" });
    expect(link.getAttribute("href")).toBe("/warehouses/wh-1/floors/floor-1/view?highlight=rack-9");
    expect(screen.getByText("Shown at the rack or zone it is in")).toBeDefined();
  });

  it("tags a bin that is on no floor plan, and offers the auto-arranged 3-D view instead", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result({ placement: null, placement_reason: "unplaced" })));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));

    expect(await screen.findByText("Not on floor plan")).toBeDefined();
    expect(screen.queryByRole("link", { name: /Show on floor/ })).toBeNull();
    expect(screen.getByRole("link", { name: "Show in 3-D for A-01" }).getAttribute("href")).toBe(
      "/warehouses/wh-1/3d?highlight=loc-1",
    );
  });

  it("says when the floor has no published layout, with the same 3-D fallback", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result({ placement: null, placement_reason: "no_published_layout" })));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    expect(await screen.findByText("Floor has no published layout")).toBeDefined();
    expect(screen.getByRole("link", { name: "Show in 3-D for A-01" })).toBeDefined();
  });

  it("says the floor plan is hidden from a caller who cannot read layouts, and offers no link into it", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result({ placement: null, placement_reason: "no_permission" })));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    expect(await screen.findByText(/Floor plan hidden/)).toBeDefined();
    expect(screen.queryByRole("link", { name: /Show on floor|Show in 3-D/ })).toBeNull();
  });

  it("handles an empty answer: nothing is stored anywhere", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found());
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    expect(await screen.findByText("Nothing is stored anywhere right now")).toBeDefined();
    expect(screen.getAllByRole("status").some((s) => s.textContent === "Nothing is stored anywhere right now.")).toBe(true);
  });

  it("shows a failed lookup in its own card with a retry, and the retry asks again", async () => {
    const user = userEvent.setup();
    locateStock.mockRejectedValueOnce(new ApiError(500, "The stock service is down", { detail: "The stock service is down" }));
    locateStock.mockResolvedValueOnce(found(result()));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));

    expect(await screen.findByText("The stock service is down")).toBeDefined();
    expect(screen.getByText("Find stock")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Stock in Bhiwandi DC" })).toBeDefined();
    expect(locateStock).toHaveBeenCalledTimes(2);
  });

  it("holds the results' place with a skeleton, and says so after 8 seconds", async () => {
    vi.useFakeTimers();
    locateStock.mockReturnValue(new Promise(() => undefined));
    renderPanel();
    await act(async () => {
      screen.getByRole("button", { name: "Find where it is" }).click();
    });
    expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
    expect((screen.getByRole("button", { name: "Search again" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      vi.advanceTimersByTime(8000);
    });
    expect(screen.getAllByRole("status").some((s) => s.textContent?.includes("taking longer than usual"))).toBe(true);
  });

  it("searches again on request", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue(found(result()));
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    await screen.findByRole("region", { name: "Stock in Bhiwandi DC" });
    await user.click(screen.getByRole("button", { name: "Search again" }));
    expect(locateStock).toHaveBeenCalledTimes(2);
  });

  it("keeps a response it cannot draw to itself: the card says so and the page around it stays", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    const broken = result({ code: undefined as unknown as string });
    locateStock.mockResolvedValue(found(broken, { ...broken, location_id: "loc-2" }));
    renderScreen(
      <div>
        <h1>The stock page</h1>
        <FindStockPanel itemId="item-1" subject="FAB-100" />
      </div>,
      [READ],
    );
    await user.click(screen.getByRole("button", { name: "Find where it is" }));

    expect(await screen.findByText("Find stock couldn't be shown")).toBeDefined();
    expect(screen.getByRole("heading", { name: "The stock page" })).toBeDefined();
  });
});
