/**
 * Bin-to-bin transfer: quantities are decimal strings checked against the bucket, every send
 * carries an Idempotency-Key (the same one for a retry of the same body), and the server's
 * refusals land where they belong — `to_location_id/different_warehouse` on the destination,
 * `insufficient-stock` with its reservations in the panel, entered data kept.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { insufficientStock, move, problem, stockRow } from "@/test-utils/tracking";
import type { OnSiteRow } from "@/lib/rollBuckets";
import { StockTransferDialog } from "./StockTransferDialog";

const transferStock = vi.fn();
const listLocations = vi.fn();

vi.mock("@/api/tracking", () => ({
  transferStock: (...args: unknown[]) => transferStock(...args),
}));
vi.mock("@/api/client", () => ({
  listLocations: (...args: unknown[]) => listLocations(...args),
}));

const ROW = stockRow({ lot_id: "lot-1", lot_no: "LOT-1", roll_id: "roll-1", roll_no: "R-001" }) as OnSiteRow;

async function renderDialog(row: OnSiteRow = ROW) {
  const user = userEvent.setup();
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<StockTransferDialog row={row} onClose={onClose} onSaved={onSaved} />);
  await waitFor(() => expect(listLocations).toHaveBeenCalled());
  await screen.findByRole("option", { name: "B-02 — Bin B-02" });
  return { user, onClose, onSaved };
}

describe("StockTransferDialog", () => {
  beforeEach(() => {
    transferStock.mockReset();
    listLocations.mockReset();
    listLocations.mockResolvedValue([
      { id: "loc-1", code: "A-01", name: "A-01" },
      { id: "loc-2", code: "B-02", name: "Bin B-02" },
    ]);
  });

  it("offers only the other locations of the bucket's own warehouse", async () => {
    await renderDialog();
    expect(listLocations).toHaveBeenCalledWith("wh-1");
    const options = within(screen.getByLabelText("To location")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Choose a location", "B-02 — Bin B-02"]);
  });

  it("holds the request until a destination and a valid quantity are given", async () => {
    const { user } = await renderDialog();
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));

    expect(await screen.findByText("Choose where the stock goes.")).toBeDefined();
    expect(screen.getByText("Enter a quantity.")).toBeDefined();
    expect(transferStock).not.toHaveBeenCalled();
  });

  it("refuses more than is not reserved, naming what can move", async () => {
    const { user } = await renderDialog(stockRow({ reserved_qty: "60", available_qty: "22.5" }) as OnSiteRow);
    await user.selectOptions(screen.getByLabelText("To location"), "loc-2");
    await user.type(screen.getByLabelText("Quantity"), "30");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));

    expect(await screen.findByText("That is more than the 22.5 m that can be moved.")).toBeDefined();
    expect(transferStock).not.toHaveBeenCalled();
  });

  it("refuses decimals of a discrete unit before the server is asked", async () => {
    const { user } = await renderDialog(stockRow({ uom: "pcs", on_hand_qty: "40", available_qty: "40" }) as OnSiteRow);
    await user.selectOptions(screen.getByLabelText("To location"), "loc-2");
    await user.type(screen.getByLabelText("Quantity"), "2.5");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));

    expect(await screen.findByText("pcs are counted in whole units — remove the decimals.")).toBeDefined();
    expect(transferStock).not.toHaveBeenCalled();
  });

  it("sends the quantity as a decimal string with the bucket's lot, roll and status, and an idempotency key", async () => {
    const { user, onSaved } = await renderDialog();
    transferStock.mockResolvedValue(move());
    await user.selectOptions(screen.getByLabelText("To location"), "loc-2");
    await user.type(screen.getByLabelText("Quantity"), "012.50");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));

    await waitFor(() => expect(transferStock).toHaveBeenCalledTimes(1));
    const [body, key] = transferStock.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toMatchObject({
      inventory_item_id: "item-1",
      from_location_id: "loc-1",
      to_location_id: "loc-2",
      quantity: "12.5",
      status: "AVAILABLE",
      lot_id: "lot-1",
      roll_id: "roll-1",
    });
    expect(typeof body["quantity"]).toBe("string");
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(onSaved).toHaveBeenCalledWith(move());
  });

  it("puts a different-warehouse refusal on the destination and keeps what was entered", async () => {
    const { user, onSaved } = await renderDialog();
    transferStock.mockRejectedValue(
      problem(422, "A bin-to-bin transfer stays inside one warehouse", [
        { field: "to_location_id", code: "different_warehouse", message: "not in the source warehouse" },
      ]),
    );
    await user.selectOptions(screen.getByLabelText("To location"), "loc-2");
    await user.type(screen.getByLabelText("Quantity"), "5");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));

    expect(
      await screen.findByText("Bin-to-bin transfers stay inside one warehouse. Choose a location in the same warehouse."),
    ).toBeDefined();
    expect(onSaved).not.toHaveBeenCalled();
    expect((screen.getByLabelText("Quantity") as HTMLInputElement).value).toBe("5");
    expect((screen.getByLabelText("To location") as HTMLSelectElement).value).toBe("loc-2");
    // Said on the field, not again at the top.
    expect(screen.queryByText("A bin-to-bin transfer stays inside one warehouse")).toBeNull();
  });

  it("shows an insufficient-stock refusal with the reservations that hold the stock", async () => {
    const { user } = await renderDialog();
    transferStock.mockRejectedValue(
      insufficientStock([
        { reservation_id: "res-1", reference_type: "order", reference_id: "abcdef0123456789", quantity: "60" },
      ]),
    );
    await user.selectOptions(screen.getByLabelText("To location"), "loc-2");
    await user.type(screen.getByLabelText("Quantity"), "40");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));

    expect(await screen.findByText("Reserved stock is in the way")).toBeDefined();
    expect(screen.getByText(/Order abcdef01 — 60 m/)).toBeDefined();
    expect(screen.getByText(/82.5 m on hand, 60 m reserved, 22.5 m available/)).toBeDefined();
    expect((screen.getByLabelText("Quantity") as HTMLInputElement).value).toBe("40");
  });

  it("replays one key for a retry of the same request, and mints a new one for a corrected quantity", async () => {
    const { user } = await renderDialog();
    transferStock.mockRejectedValueOnce(problem(500, "Something broke", [], "internal"));
    transferStock.mockResolvedValue(move());
    await user.selectOptions(screen.getByLabelText("To location"), "loc-2");
    await user.type(screen.getByLabelText("Quantity"), "5");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));
    await screen.findByText("Something broke");

    await user.click(screen.getByRole("button", { name: "Transfer stock" }));
    await waitFor(() => expect(transferStock).toHaveBeenCalledTimes(2));
    expect(transferStock.mock.calls[1]?.[1]).toBe(transferStock.mock.calls[0]?.[1]);
  });

  it("mints a new key when the quantity changes", async () => {
    const { user } = await renderDialog();
    transferStock.mockRejectedValueOnce(problem(500, "Something broke", [], "internal"));
    transferStock.mockResolvedValue(move());
    await user.selectOptions(screen.getByLabelText("To location"), "loc-2");
    await user.type(screen.getByLabelText("Quantity"), "5");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));
    await screen.findByText("Something broke");

    await user.clear(screen.getByLabelText("Quantity"));
    await user.type(screen.getByLabelText("Quantity"), "6");
    await user.click(screen.getByRole("button", { name: "Transfer stock" }));
    await waitFor(() => expect(transferStock).toHaveBeenCalledTimes(2));
    expect(transferStock.mock.calls[1]?.[1]).not.toBe(transferStock.mock.calls[0]?.[1]);
  });
});
