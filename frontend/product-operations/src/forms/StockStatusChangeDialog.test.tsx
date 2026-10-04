/**
 * Status change: Available to Quality hold and the like. The quantity is a decimal string checked
 * against what is not reserved; the server's field and insufficient-stock refusals land in place.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { ApiError, PermissionProvider } from "@opsgrids/client";
import userEvent from "@testing-library/user-event";

import type { OnSiteRow } from "@/lib/rollBuckets";
import { insufficientStock, move, problem, stockRow } from "@/test-utils/tracking";
import { StockStatusChangeDialog } from "./StockStatusChangeDialog";

const changeStockStatus = vi.fn();

vi.mock("@/api/tracking", () => ({
  changeStockStatus: (...args: unknown[]) => changeStockStatus(...args),
}));

const ROW = stockRow({ roll_id: "roll-1", roll_no: "R-001", lot_id: "lot-1", lot_no: "LOT-1" }) as OnSiteRow;

function renderDialog(row: OnSiteRow = ROW) {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<StockStatusChangeDialog row={row} onClose={vi.fn()} onSaved={onSaved} />);
  return { user, onSaved };
}

describe("StockStatusChangeDialog", () => {
  beforeEach(() => {
    changeStockStatus.mockReset();
  });

  it("offers every on-site status except the one the stock is already in", () => {
    renderDialog();
    const options = within(screen.getByLabelText("New status")).getAllByRole("option");
    const labels = options.map((o) => o.textContent);
    expect(labels).not.toContain("Available");
    expect(labels).toContain("Quality hold");
    expect(labels).not.toContain("In transit");
    expect(labels).not.toContain("At job worker");
  });

  it("holds the request until a status and a valid quantity are given", async () => {
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Change status" }));

    expect(await screen.findByText("Choose the new status.")).toBeDefined();
    expect(screen.getByText("Enter a quantity.")).toBeDefined();
    expect(changeStockStatus).not.toHaveBeenCalled();
  });

  it("refuses more than the bucket holds unreserved", async () => {
    const { user } = renderDialog(stockRow({ reserved_qty: "10" }) as OnSiteRow);
    await user.selectOptions(screen.getByLabelText("New status"), "QUALITY_HOLD");
    await user.type(screen.getByLabelText("Quantity"), "80");
    await user.click(screen.getByRole("button", { name: "Change status" }));

    expect(await screen.findByText("That is more than the 72.5 m that can change status.")).toBeDefined();
    expect(changeStockStatus).not.toHaveBeenCalled();
  });

  it("sends from and to status, the decimal-string quantity, the bucket's lot and roll, and a key", async () => {
    const { user, onSaved } = renderDialog();
    changeStockStatus.mockResolvedValue(move());
    await user.selectOptions(screen.getByLabelText("New status"), "QUALITY_HOLD");
    await user.type(screen.getByLabelText("Quantity"), "20.25");
    await user.click(screen.getByRole("button", { name: "Change status" }));

    await waitFor(() => expect(changeStockStatus).toHaveBeenCalledTimes(1));
    const [body, key] = changeStockStatus.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toMatchObject({
      inventory_item_id: "item-1",
      location_id: "loc-1",
      from_status: "AVAILABLE",
      to_status: "QUALITY_HOLD",
      quantity: "20.25",
      lot_id: "lot-1",
      roll_id: "roll-1",
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(onSaved).toHaveBeenCalled();
  });

  it("shows the reservations when stock is there but promised, and keeps the entry", async () => {
    const { user } = renderDialog();
    changeStockStatus.mockRejectedValue(
      insufficientStock([
        { reservation_id: "res-9", reference_type: "sales_order", reference_id: "11223344aabb", quantity: "60" },
      ]),
    );
    await user.selectOptions(screen.getByLabelText("New status"), "QUARANTINE");
    await user.type(screen.getByLabelText("Quantity"), "50");
    await user.click(screen.getByRole("button", { name: "Change status" }));

    expect(await screen.findByText("Reserved stock is in the way")).toBeDefined();
    expect(screen.getByText(/Sales order 11223344 — 60 m/)).toBeDefined();
    expect((screen.getByLabelText("Quantity") as HTMLInputElement).value).toBe("50");
  });

  it("puts a quantity refusal from the server on the quantity field", async () => {
    const { user } = renderDialog();
    changeStockStatus.mockRejectedValue(
      problem(422, "A quantity here must be greater than zero", [
        { field: "quantity", code: "invalid_quantity", message: "must be > 0" },
      ]),
    );
    await user.selectOptions(screen.getByLabelText("New status"), "QUARANTINE");
    await user.type(screen.getByLabelText("Quantity"), "1");
    await user.click(screen.getByRole("button", { name: "Change status" }));

    expect(await screen.findByText("must be > 0")).toBeDefined();
  });

  describe("releasing stock out of a hold", () => {
    const HELD = stockRow({ status: "QUARANTINE" }) as OnSiteRow;

    function renderWithGrants(row: OnSiteRow, grants: string[]) {
      const user = userEvent.setup();
      render(
        <PermissionProvider grants={grants} entitlements={[]}>
          <StockStatusChangeDialog row={row} onClose={vi.fn()} onSaved={vi.fn()} />
        </PermissionProvider>,
      );
      return { user };
    }

    function optionLabels(): (string | null)[] {
      return within(screen.getByLabelText("New status")).getAllByRole("option").map((o) => o.textContent);
    }

    it("offers Available and Work in progress to a caller with stock.adjust", () => {
      renderWithGrants(HELD, ["operations.inventory.stock.adjust:ORG"]);
      expect(optionLabels()).toContain("Available");
      expect(optionLabels()).toContain("Work in progress");
    });

    it("withholds the release targets from a caller without it, and says why", () => {
      renderWithGrants(HELD, ["operations.inventory.stock.transfer:ORG"]);
      expect(optionLabels()).not.toContain("Available");
      expect(optionLabels()).not.toContain("Work in progress");
      expect(optionLabels()).toContain("Damaged");
      expect(screen.getByText(/needs the operations\.inventory\.stock\.adjust permission/)).toBeDefined();
    });

    it("keeps Available offered for stock that is not in a hold, with transfer alone", () => {
      renderWithGrants(
        stockRow({ status: "WIP" }) as OnSiteRow,
        ["operations.inventory.stock.transfer:ORG"],
      );
      expect(optionLabels()).toContain("Available");
      expect(screen.queryByText(/needs the operations\.inventory\.stock\.adjust permission/)).toBeNull();
    });

    it("words a 403 from the server so it names the permission, and keeps the entry", async () => {
      changeStockStatus.mockRejectedValue(
        new ApiError(403, "Forbidden", { type: "about:blank", title: "Forbidden", status: 403, detail: "Forbidden" }),
      );
      const { user } = renderWithGrants(HELD, ["operations.inventory.stock.adjust:ORG"]);
      await user.selectOptions(screen.getByLabelText("New status"), "AVAILABLE");
      await user.type(screen.getByLabelText("Quantity"), "5");
      await user.click(screen.getByRole("button", { name: "Change status" }));

      expect(await screen.findByText(/needs operations\.inventory\.stock\.adjust/)).toBeDefined();
      expect((screen.getByLabelText("Quantity") as HTMLInputElement).value).toBe("5");
    });
  });
});
