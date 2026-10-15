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
const setItemAttribute = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listItemCategories: (...args: unknown[]) => listItemCategories(...args),
  listUoms: (...args: unknown[]) => listUoms(...args),
  createInventoryItem: (...args: unknown[]) => createInventoryItem(...args),
  updateInventoryItem: (...args: unknown[]) => updateInventoryItem(...args),
  setItemAttribute: (...args: unknown[]) => setItemAttribute(...args),
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
  receipt_tolerance_pct: "0",
  is_active: true,
  selling_price_minor: null,
  selling_price_currency: null,
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
    setItemAttribute.mockReset();
  });

  it("holds Continue on item details until the required fields are set", async () => {
    const user = userEvent.setup();
    renderDialog();

    await screen.findByRole("heading", { name: "Item details" });
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // The SKU is optional now, so the first thing missing is the name.
    expect(await screen.findByText("Give the item a name.")).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Item master" })).toBeNull();
    expect(createInventoryItem).not.toHaveBeenCalled();
  });

  it("refuses a typed SKU with characters the server would reject", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/SKU/), "bad sku");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("Letters, numbers, dots, hyphens and underscores only.")).toBeDefined();
    expect(createInventoryItem).not.toHaveBeenCalled();
  });

  it("shows the SKU as optional, with the auto-generate hint", async () => {
    renderDialog();

    const field = await screen.findByLabelText(/SKU/);
    expect(field).toHaveProperty("required", false);
    expect(screen.getByText("Leave blank to generate one automatically")).toBeDefined();
  });

  it("creates with no sku in the body when it is left blank, and hands back the generated code", async () => {
    const user = userEvent.setup();
    createInventoryItem.mockResolvedValue({ ...CREATED, sku: "ITM-000001" });
    const { onSaved } = renderDialog();

    await user.type(screen.getByLabelText("Name"), "Cotton yarn");
    await user.selectOptions(screen.getByLabelText("Category"), "raw_materials");
    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Bengaluru DC");
    await user.type(screen.getByLabelText("Unit of measure"), "MT");
    await user.type(screen.getByLabelText("Unit value"), "1500");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Item master" });
    await user.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => expect(createInventoryItem).toHaveBeenCalledTimes(1));
    const body = createInventoryItem.mock.calls[0]?.[0] as Record<string, unknown>;
    expect("sku" in body).toBe(false);
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ sku: "ITM-000001" }));
  });

  it("moves to item master on Continue, and the step rail returns to item details", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/SKU/), "RM-100");
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
    expect(screen.queryByLabelText(/SKU/)).toBeNull();

    await user.click(screen.getByRole("button", { name: /Item details/ }));
    expect(await screen.findByLabelText(/SKU/)).toHaveProperty("value", "RM-100");
  });

  it("creates the item from the item master step", async () => {
    const user = userEvent.setup();
    createInventoryItem.mockResolvedValue(CREATED);
    const { onSaved } = renderDialog();

    await user.type(screen.getByLabelText(/SKU/), "RM-100");
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

  async function reachItemMaster(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText(/SKU/), "FAB-100");
    await user.type(screen.getByLabelText("Name"), "Cotton poplin");
    await user.selectOptions(screen.getByLabelText("Category"), "raw_materials");
    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Bengaluru DC");
    await user.type(screen.getByLabelText("Unit of measure"), "m");
    await user.type(screen.getByLabelText("Unit value"), "250");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    // Textile facts are not on every item: they appear only when asked for.
    expect(screen.queryByRole("heading", { name: "Textile attributes" })).toBeNull();
    expect(screen.queryByLabelText(/GSM/)).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Add textile attributes" }));
    await screen.findByRole("heading", { name: "Textile attributes" });
  }

  it("offers the textile preset, and saves each filled field as a typed attribute after the item exists", async () => {
    const user = userEvent.setup();
    createInventoryItem.mockResolvedValue(CREATED);
    setItemAttribute.mockResolvedValue({});
    const { onSaved } = renderDialog();
    await reachItemMaster(user);

    for (const label of [
      /GSM/,
      /Width \(mm\)/,
      /Shade/,
      /Composition/,
      /Count/,
      /Construction/,
      /Finish/,
      /Grade/,
      /Season/,
      /Collection/,
    ]) {
      expect(screen.getByLabelText(label)).toBeDefined();
    }

    await user.type(screen.getByLabelText(/GSM/), "120");
    await user.type(screen.getByLabelText(/Width \(mm\)/), "1500");
    await user.type(screen.getByLabelText(/Shade/), "Indigo");
    await user.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
    expect(setItemAttribute).toHaveBeenCalledTimes(3);
    expect(setItemAttribute).toHaveBeenCalledWith("item-1", "gsm", { value_type: "number", value_number: 120 });
    expect(setItemAttribute).toHaveBeenCalledWith("item-1", "width_mm", { value_type: "number", value_number: 1500 });
    expect(setItemAttribute).toHaveBeenCalledWith("item-1", "shade", { value_type: "text", value_text: "Indigo" });
    // Created first: the attributes are keyed on the new item's id.
    expect(createInventoryItem.mock.invocationCallOrder[0] ?? 0).toBeLessThan(
      setItemAttribute.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it("sets no attributes when the preset is left blank", async () => {
    const user = userEvent.setup();
    createInventoryItem.mockResolvedValue(CREATED);
    const { onSaved } = renderDialog();
    await reachItemMaster(user);
    await user.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(setItemAttribute).not.toHaveBeenCalled();
  });

  it("refuses a GSM that is not a whole number before creating anything", async () => {
    const user = userEvent.setup();
    const { onSaved } = renderDialog();
    await reachItemMaster(user);
    await user.type(screen.getByLabelText(/GSM/), "12.5");
    await user.click(screen.getByRole("button", { name: "Create item" }));

    expect(await screen.findByText("GSM is a whole number, like 150.")).toBeDefined();
    expect(createInventoryItem).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("creates a non-textile item without ever showing fabric fields", async () => {
    const user = userEvent.setup();
    createInventoryItem.mockResolvedValue(CREATED);
    const { onSaved } = renderDialog();
    await user.type(screen.getByLabelText(/SKU/), "BOLT-100");
    await user.type(screen.getByLabelText("Name"), "Hex bolt");
    await user.selectOptions(screen.getByLabelText("Category"), "raw_materials");
    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Bengaluru DC");
    await user.type(screen.getByLabelText("Unit of measure"), "pcs");
    await user.type(screen.getByLabelText("Unit value"), "5");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("button", { name: "Add textile attributes" })).toBeDefined();
    expect(screen.queryByLabelText(/GSM/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
    expect(setItemAttribute).not.toHaveBeenCalled();
  });

  it("still finishes when an attribute fails: the item exists, so nothing is created twice", async () => {
    const user = userEvent.setup();
    createInventoryItem.mockResolvedValue(CREATED);
    setItemAttribute.mockImplementation((_id: string, key: string) =>
      key === "shade" ? Promise.reject(new Error("offline")) : Promise.resolve({}),
    );
    const { onSaved } = renderDialog();
    await reachItemMaster(user);
    await user.type(screen.getByLabelText(/GSM/), "120");
    await user.type(screen.getByLabelText(/Shade/), "Indigo");
    await user.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
    expect(createInventoryItem).toHaveBeenCalledTimes(1);
    expect(setItemAttribute).toHaveBeenCalledTimes(2);
  });
});
