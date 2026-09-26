/**
 * Create an inventory item: two steps behind the shared `Stepper` — item details, then the
 * optional item master fields. `FormDialog`'s own footer doubles as each step's forward action,
 * so Continue on the first step never reaches the service until the second step's Create.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { InventoryItemOut } from "@/api/types";
import { InventoryItemFormDialog } from "./InventoryItemFormDialog";

const listWarehouses = vi.fn();
const listItemCategories = vi.fn();
const listUoms = vi.fn();
const createInventoryItem = vi.fn();
const updateInventoryItem = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listItemCategories: (...args: unknown[]) => listItemCategories(...args),
  listUoms: (...args: unknown[]) => listUoms(...args),
  createInventoryItem: (...args: unknown[]) => createInventoryItem(...args),
  updateInventoryItem: (...args: unknown[]) => updateInventoryItem(...args),
}));

const CREATED: InventoryItemOut = {
  id: "item-1",
  sku: "RM-100",
  name: "Cotton yarn",
  category: "raw_materials",
  warehouse: "Bengaluru DC",
  on_hand: 0,
  on_hand_qty: "0",
  reserved_qty: "0",
  available_qty: "0",
  uom: "MT",
  reorder_point: 5,
  unit_value_minor: 150000,
  currency: "INR",
  updated_at: "2026-09-25T00:00:00Z",
  item_category_id: null,
  secondary_uom_id: null,
  hsn_code: "",
  track_lot: false,
  track_roll: false,
  track_expiry: false,
  track_quality: false,
  is_active: true,
};

function renderDialog() {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<InventoryItemFormDialog item={null} onClose={onClose} onSaved={onSaved} />);
  return { onClose, onSaved };
}

describe("InventoryItemFormDialog", () => {
  beforeEach(() => {
    listWarehouses.mockResolvedValue({ items: [{ name: "Bengaluru DC" }], total: 1, page: 1, size: 200 });
    listItemCategories.mockResolvedValue({ items: [], total: 0, page: 1, size: 200 });
    listUoms.mockResolvedValue({ items: [], total: 0, page: 1, size: 200 });
    createInventoryItem.mockReset();
    updateInventoryItem.mockReset();
  });

  it("holds Continue on item details until the required fields are set", async () => {
    const user = userEvent.setup();
    renderDialog();

    await screen.findByRole("heading", { name: "Item details" });
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("Letters, numbers, dots, hyphens and underscores only.")).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Item master" })).toBeNull();
    expect(createInventoryItem).not.toHaveBeenCalled();
  });

  it("moves to item master on Continue, and the step rail returns to item details", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("SKU"), "RM-100");
    await user.type(screen.getByLabelText("Name"), "Cotton yarn");
    await user.selectOptions(screen.getByLabelText("Category"), "raw_materials");
    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Bengaluru DC");
    await user.type(screen.getByLabelText("Unit of measure"), "MT");
    await user.clear(screen.getByLabelText("Reorder point"));
    await user.type(screen.getByLabelText("Reorder point"), "5");
    await user.type(screen.getByLabelText("Unit value"), "1500");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Item master" })).toBeDefined();
    expect(screen.queryByLabelText("SKU")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Item details/ }));
    expect(await screen.findByLabelText("SKU")).toHaveProperty("value", "RM-100");
  });

  it("creates the item from the item master step", async () => {
    const user = userEvent.setup();
    createInventoryItem.mockResolvedValue(CREATED);
    const { onSaved } = renderDialog();

    await user.type(screen.getByLabelText("SKU"), "RM-100");
    await user.type(screen.getByLabelText("Name"), "Cotton yarn");
    await user.selectOptions(screen.getByLabelText("Category"), "raw_materials");
    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Bengaluru DC");
    await user.type(screen.getByLabelText("Unit of measure"), "MT");
    await user.clear(screen.getByLabelText("Reorder point"));
    await user.type(screen.getByLabelText("Reorder point"), "5");
    await user.type(screen.getByLabelText("Unit value"), "1500");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "Item master" });
    await user.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => expect(createInventoryItem).toHaveBeenCalledTimes(1));
    expect(onSaved).toHaveBeenCalledWith(CREATED);
    const body = createInventoryItem.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body.sku).toBe("RM-100");
    expect(body.warehouse).toBe("Bengaluru DC");
    expect(body.unit_value_minor).toBe(150000);
  });
});
