/**
 * Lot detail: genealogy from the trace (both directions, linked), the lot's stock and rolls, and
 * the loading / error states of the page.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { LotTraceOut } from "@/api/trackingTypes";
import { renderScreen } from "@/test-utils/renderScreen";
import { lot, roll, stockRow } from "@/test-utils/tracking";
import { LotDetailPage } from "./LotDetail";

const getLotTrace = vi.fn();
const listStock = vi.fn();
const listRolls = vi.fn();
const locateStock = vi.fn();

vi.mock("@/api/tracking", () => ({
  getLotTrace: (...args: unknown[]) => getLotTrace(...args),
  listStock: (...args: unknown[]) => listStock(...args),
  listRolls: (...args: unknown[]) => listRolls(...args),
  locateStock: (...args: unknown[]) => locateStock(...args),
  transferStock: () => Promise.reject(new Error("not used")),
  changeStockStatus: () => Promise.reject(new Error("not used")),
}));
vi.mock("@/api/client", () => ({
  listLocations: () => Promise.resolve([]),
}));

const READ = "operations.inventory.stock.read:ORG";

function trace(overrides: Partial<LotTraceOut> = {}): LotTraceOut {
  return { lot: lot({ id: "lot-1", lot_no: "LOT-1", on_hand_qty: "82.5", quality_status: "APPROVED" }), ancestors: [], descendants: [], ...overrides };
}

function renderLot(grants = [READ]) {
  return renderScreen(<LotDetailPage />, grants, { path: "/inventory/lots/:id", at: "/inventory/lots/lot-1" });
}

describe("LotDetailPage", () => {
  beforeEach(() => {
    getLotTrace.mockReset();
    listStock.mockReset();
    listRolls.mockReset();
    getLotTrace.mockResolvedValue(trace());
    listStock.mockResolvedValue({ items: [stockRow({ lot_id: "lot-1", lot_no: "LOT-1" })], next_cursor: null });
    listRolls.mockResolvedValue({ items: [roll({ id: "r1", roll_no: "R-001" })], next_cursor: null });
  });

  it("explains a missing grant", async () => {
    renderLot([]);
    expect(await screen.findByText("You don't have access to this lot")).toBeDefined();
    expect(getLotTrace).not.toHaveBeenCalled();
  });

  it("shows the lot's figures, its quality status as a word, its stock and its rolls", async () => {
    renderLot();
    expect(await screen.findByRole("heading", { name: "LOT-1" })).toBeDefined();
    expect(screen.getAllByText("Approved").length).toBeGreaterThan(0);
    expect(screen.getByText("82.5 m")).toBeDefined();
    expect(await screen.findByText("A-01")).toBeDefined();
    expect(await screen.findByRole("link", { name: "R-001" })).toBeDefined();
    expect(listStock).toHaveBeenCalledWith(expect.objectContaining({ lot_id: "lot-1" }));
    expect(listRolls).toHaveBeenCalledWith(expect.objectContaining({ lot_id: "lot-1" }));
  });

  it("links the lots it came from and went into", async () => {
    getLotTrace.mockResolvedValue(
      trace({
        ancestors: [{ id: "lot-0", number: "LOT-0", depth: 1, from_id: "lot-1", relation: "BLEND", move_id: null, lot_id: null, lot_no: null }],
        descendants: [{ id: "lot-2", number: "LOT-2", depth: 1, from_id: "lot-1", relation: "REWORK", move_id: null, lot_id: null, lot_no: null }],
      }),
    );
    renderLot();

    expect((await screen.findByRole("link", { name: "LOT-0" })).getAttribute("href")).toBe("/inventory/lots/lot-0");
    expect(screen.getByRole("link", { name: "LOT-2" }).getAttribute("href")).toBe("/inventory/lots/lot-2");
    expect(screen.getByText("blend")).toBeDefined();
    expect(screen.getByText("rework")).toBeDefined();
  });

  it("says plainly when the lot has no genealogy", async () => {
    renderLot();
    expect(await screen.findByText("This lot has no recorded parent lots.")).toBeDefined();
    expect(screen.getByText("Nothing has been made from this lot.")).toBeDefined();
  });

  it("shows an error with a retry", async () => {
    const user = userEvent.setup();
    getLotTrace.mockRejectedValueOnce(new ApiError(500, "Trace unavailable", { detail: "Trace unavailable" }));
    renderLot();

    expect(await screen.findByText("Trace unavailable")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "LOT-1" })).toBeDefined();
  });

  it("shows empty stock and rolls as states of their own", async () => {
    listStock.mockResolvedValue({ items: [], next_cursor: null });
    listRolls.mockResolvedValue({ items: [], next_cursor: null });
    renderLot();

    expect(await screen.findByText("No stock on the books yet")).toBeDefined();
    expect(await screen.findByText("No rolls yet")).toBeDefined();
  });

  it("offers Find stock for this lot, asking only when clicked and narrowing the search to the lot", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue({ results: [] });
    renderLot();

    await screen.findByRole("heading", { name: "LOT-1" });
    expect(screen.getByText("Which bins hold lot LOT-1 right now, and where they are.")).toBeDefined();
    expect(locateStock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    expect(locateStock).toHaveBeenCalledWith({ inventory_item_id: "item-1", lot_id: "lot-1", roll_id: undefined });
    expect(await screen.findByText("Nothing is stored anywhere right now")).toBeDefined();
  });
});
