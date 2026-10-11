/**
 * `/inventory/scan` end to end against a mocked API: a scan resolves, the buttons that appear are
 * exactly the intersection of what the server allowed and what this build can do, a move posts
 * through the stock endpoint with an idempotency key, and every failure is a sentence.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ScanEntityOut, ScanOut, StockRow } from "@/api/scan";
import { ApiError } from "@opsgrids/client";
import { registerRoute, resetRoutes } from "@/lib/routeRegistry";
import { renderScreen } from "@/test-utils/renderScreen";

const resolveScan = vi.fn();
const listStockFor = vi.fn();
const transferStock = vi.fn();
const changeStockStatus = vi.fn();

vi.mock("@/api/scan", () => ({
  resolveScan: (...a: unknown[]) => resolveScan(...a),
  listStockFor: (...a: unknown[]) => listStockFor(...a),
  transferStock: (...a: unknown[]) => transferStock(...a),
  changeStockStatus: (...a: unknown[]) => changeStockStatus(...a),
}));

import { ScanPage } from "./ScanPage";

const ROLL: ScanEntityOut = {
  type: "roll",
  id: "roll-1",
  code: "R-1",
  title: "R-1",
  subtitle: "COT-180 · LOT-A",
  uom: "m",
  on_hand_qty: "82.5",
  inventory_item_id: "item-1",
  sku: "COT-180",
  lot_id: "lot-1",
  lot_no: "LOT-A",
  roll_id: "roll-1",
  location_id: null,
  warehouse_id: null,
  track_lot: true,
  track_roll: true,
  status: "ACTIVE",
};

const BIN_B: ScanEntityOut = {
  ...ROLL,
  type: "location",
  id: "loc-b",
  code: "BIN-B",
  title: "Bin B",
  subtitle: "Weaving Store · BIN-B",
  on_hand_qty: null,
  inventory_item_id: null,
  roll_id: null,
  lot_id: null,
  location_id: "loc-b",
  warehouse_id: "wh-1",
};

const BUCKET: StockRow = {
  id: "bucket-1",
  inventory_item_id: "item-1",
  sku: "COT-180",
  item_name: "Cotton",
  uom: "m",
  warehouse_id: "wh-1",
  warehouse_code: "WH-WEAVE",
  location_id: "loc-a",
  location_code: "DEFAULT",
  status: "AVAILABLE",
  lot_id: "lot-1",
  lot_no: "LOT-A",
  roll_id: "roll-1",
  roll_no: "R-1",
  on_hand_qty: "82.5",
  reserved_qty: "0",
  available_qty: "82.5",
};

const scanOut = (entity: ScanEntityOut, actions: ScanOut["allowed_actions"]): ScanOut => ({
  payload: `og:${entity.type}:${entity.code}`,
  type: entity.type,
  entity,
  allowed_actions: actions,
});

const GRANTS = ["operations.inventory.stock.read:ORG"];

async function scan(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Type or scan a code"), `${text}{Enter}`);
  return user;
}

beforeEach(() => {
  resetRoutes();
  for (const r of ["purchase-orders", "fulfilment/tasks", "counting", "inventory/:id"]) {
    registerRoute(r);
  }
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
});

describe("resolving a scan", () => {
  it("shows what was scanned, the exact quantity, and only the actions it can do", async () => {
    resolveScan.mockResolvedValue(scanOut(ROLL, ["view", "transfer", "status_change", "inspect"]));
    renderScreen(<ScanPage />, GRANTS);
    await scan("og:roll:R-1");
    expect(await screen.findByText("R-1")).toBeTruthy();
    expect(resolveScan).toHaveBeenCalledWith("og:roll:R-1");
    expect(screen.getByText("82.5")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Move/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Hold or release/ })).toBeTruthy();
    // Inspect was allowed by the server but this build has no inspections screen: not drawn.
    expect(screen.queryByText("Inspect")).toBeNull();
    expect(screen.getByRole("link", { name: /Details/ }).getAttribute("href")).toBe("/inventory/item-1");
  });

  it("draws a quality inspection link once that screen exists", async () => {
    registerRoute("quality/inspections");
    resolveScan.mockResolvedValue(scanOut(ROLL, ["view", "inspect"]));
    renderScreen(<ScanPage />, GRANTS);
    await scan("R-1");
    expect((await screen.findByRole("link", { name: /Inspect/ })).getAttribute("href")).toBe(
      "/quality/inspections",
    );
  });

  it("a read-only caller sees the record and no way to change it", async () => {
    resolveScan.mockResolvedValue(scanOut(ROLL, ["view"]));
    renderScreen(<ScanPage />, GRANTS);
    await scan("R-1");
    await screen.findByText("R-1");
    expect(screen.queryByRole("button", { name: /Move/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Hold/ })).toBeNull();
  });

  it("says plainly when nothing has that code", async () => {
    resolveScan.mockRejectedValue(new ApiError(404, "not found", null));
    renderScreen(<ScanPage />, GRANTS);
    await scan("NOPE");
    expect(await screen.findByText("Nothing in this organization has that code.")).toBeTruthy();
    // The scanner is still there for the next try.
    expect(screen.getByLabelText("Type or scan a code")).toBeTruthy();
  });

  it("reports a malformed label without calling the server", async () => {
    renderScreen(<ScanPage />, GRANTS);
    await scan("og:box:X");
    expect(await screen.findByText(/unknown label type/)).toBeTruthy();
    expect(resolveScan).not.toHaveBeenCalled();
  });

  it("surfaces a forbidden lookup", async () => {
    resolveScan.mockRejectedValue(new ApiError(403, "no", null));
    renderScreen(<ScanPage />, GRANTS);
    await scan("R-1");
    expect(await screen.findByText("You are not allowed to look up stock.")).toBeTruthy();
  });
});

describe("moving stock", () => {
  async function toTransfer() {
    resolveScan.mockResolvedValueOnce(scanOut(ROLL, ["view", "transfer"]));
    listStockFor.mockResolvedValue([BUCKET]);
    renderScreen(<ScanPage />, GRANTS);
    const user = await scan("R-1");
    await user.click(await screen.findByRole("button", { name: /Move/ }));
    await screen.findByText("Scan the destination bin");
    return user;
  }

  it("scans the destination bin, then posts one transfer with an idempotency key", async () => {
    const user = await toTransfer();
    resolveScan.mockResolvedValueOnce(scanOut(BIN_B, ["view"]));
    transferStock.mockResolvedValue({ move_id: "m1", kind: "relocation", replayed: false });
    // The destination scanner is the page's second text field.
    const fields = screen.getAllByLabelText("Type or scan a code");
    await user.type(fields[fields.length - 1] as HTMLElement, "og:location:BIN-B{Enter}");
    await screen.findByText("To Bin B");
    await user.clear(screen.getByLabelText("Quantity (m)"));
    await user.type(screen.getByLabelText("Quantity (m)"), "30");
    await user.click(screen.getByRole("button", { name: "Confirm move" }));
    await waitFor(() => expect(transferStock).toHaveBeenCalledTimes(1));
    const [body, key] = transferStock.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toEqual({
      inventory_item_id: "item-1",
      from_location_id: "loc-a",
      to_location_id: "loc-b",
      quantity: "30",
      status: "AVAILABLE",
      lot_id: "lot-1",
      roll_id: "roll-1",
    });
    expect(key).toMatch(/[0-9a-f-]{36}/);
    expect(await screen.findByText(/Moved 30 m to Bin B/)).toBeTruthy();
  });

  it("refuses a bin in another warehouse and a label that is not a bin", async () => {
    const user = await toTransfer();
    const field = () => {
      const all = screen.getAllByLabelText("Type or scan a code");
      return all[all.length - 1] as HTMLElement;
    };
    resolveScan.mockResolvedValueOnce(
      scanOut({ ...BIN_B, warehouse_id: "wh-OTHER" }, ["view"]),
    );
    await user.type(field(), "og:location:FAR{Enter}");
    expect(await screen.findByText(/another warehouse/)).toBeTruthy();
    resolveScan.mockResolvedValueOnce(scanOut(ROLL, ["view"]));
    await user.type(field(), "og:roll:R-1{Enter}");
    expect(await screen.findByText(/not a bin/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Confirm move" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("will not move more than is free", async () => {
    const user = await toTransfer();
    await user.clear(screen.getByLabelText("Quantity (m)"));
    await user.type(screen.getByLabelText("Quantity (m)"), "90");
    expect(await screen.findByText("Only 82.5 m is free to move here")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Confirm move" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the server's refusal when the move is rejected", async () => {
    const user = await toTransfer();
    resolveScan.mockResolvedValueOnce(scanOut(BIN_B, ["view"]));
    transferStock.mockRejectedValue(
      new ApiError(409, "Insufficient stock: 82.5 on hand, 70 reserved", null),
    );
    const all = screen.getAllByLabelText("Type or scan a code");
    await user.type(all[all.length - 1] as HTMLElement, "BIN-B{Enter}");
    await screen.findByText("To Bin B");
    await user.click(screen.getByRole("button", { name: "Confirm move" }));
    expect(await screen.findByText(/Insufficient stock: 82.5 on hand, 70 reserved/)).toBeTruthy();
  });

  it("holds stock through the status-change endpoint", async () => {
    resolveScan.mockResolvedValueOnce(scanOut(ROLL, ["view", "status_change"]));
    listStockFor.mockResolvedValue([BUCKET]);
    changeStockStatus.mockResolvedValue({ move_id: "m2", kind: "relocation", replayed: false });
    renderScreen(<ScanPage />, GRANTS);
    const user = await scan("R-1");
    await user.click(await screen.findByRole("button", { name: /Hold or release/ }));
    await user.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(changeStockStatus).toHaveBeenCalledTimes(1));
    expect(changeStockStatus.mock.calls[0]?.[0]).toMatchObject({
      from_status: "AVAILABLE",
      to_status: "QUALITY_HOLD",
      quantity: "82.5",
      roll_id: "roll-1",
    });
    expect(await screen.findByText(/now quality hold/)).toBeTruthy();
  });

  it("says when there is nothing free to move", async () => {
    resolveScan.mockResolvedValueOnce(scanOut(ROLL, ["view", "transfer"]));
    listStockFor.mockResolvedValue([{ ...BUCKET, available_qty: "0", reserved_qty: "82.5" }]);
    renderScreen(<ScanPage />, GRANTS);
    const user = await scan("R-1");
    await user.click(await screen.findByRole("button", { name: /Move/ }));
    expect(await screen.findByText("Nothing free to move")).toBeTruthy();
  });
});
