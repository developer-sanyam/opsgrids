/**
 * The new-order route: two steps (customer & warehouse, then line items) behind the shared
 * `Stepper`, the same shape `PurchaseOrderForm.test.tsx` exercises for its own form. Continue on
 * the first step validates what belongs to it and holds the user there on a problem; the create
 * only ever fires from the last step.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type { InventoryItemOut, OrderOut } from "@/api/types";
import { OrderFormPage } from "./OrderForm";

const listWarehouses = vi.fn();
const searchOrderableItems = vi.fn();
const createOrder = vi.fn();
const quoteOrder = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  searchOrderableItems: (...args: unknown[]) => searchOrderableItems(...args),
  createOrder: (...args: unknown[]) => createOrder(...args),
  quoteOrder: (...args: unknown[]) => quoteOrder(...args),
}));

const USER: MeOut = {
  user_id: "user-1",
  email: "raiser@example.com",
  full_name: "Asha Rao",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: [],
  amr: [],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const ITEM: InventoryItemOut = {
  id: "item-1",
  sku: "RM-STL-4501",
  name: "Cold-rolled steel coil",
  category: "raw_materials",
  warehouse: "Pune Plant",
  on_hand: 82,
  on_hand_qty: "82",
  reserved_qty: "0",
  available_qty: "82",
  reorder_point: 40,
  uom: "MT",
  unit_value_minor: 62400,
  currency: "INR",
  updated_at: "2026-09-01T00:00:00Z",
  item_category_id: null,
  secondary_uom_id: null,
  hsn_code: "",
  track_lot: false,
  track_roll: false,
  track_expiry: false,
  track_quality: false,
  receipt_tolerance_pct: "0",
  is_active: true,
  selling_price_minor: 74900,
  selling_price_currency: "INR",
};

const CREATED: OrderOut = {
  id: "ord-new",
  number: "ORD-2026-0301",
  channel: "phone",
  status: "DRAFT",
  version: 0,
  warehouse_id: "wh-1",
  customer_id: null,
  customer_name: "Rohan Malhotra",
  customer_phone: "",
  subtotal_minor: 0,
  total_minor: 0,
  currency: "INR",
  lines: [],
  created_at: "2026-09-28T09:00:00Z",
  updated_at: "2026-09-28T09:00:00Z",
};

function renderPage() {
  const router = createMemoryRouter(
    [
      { path: "/orders/new", element: <OrderFormPage /> },
      { path: "/orders", element: <h1>Orders</h1> },
      { path: "/orders/:id", element: <h1>Order detail</h1> },
    ],
    { initialEntries: ["/orders/new"] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <RouterProvider router={router} />
    </AuthContext.Provider>,
  );
  return router;
}

async function chooseItem(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("combobox", { name: "Item" }), "steel");
  await waitFor(() => expect(screen.getByRole("option", { name: /RM-STL-4501/ })).toBeDefined());
  await user.keyboard("{ArrowDown}{Enter}");
}

describe("OrderFormPage", () => {
  beforeEach(() => {
    listWarehouses.mockResolvedValue({
      items: [{ id: "wh-1", code: "WH-PUN", name: "Pune Plant", location: "" }],
      total: 1,
      page: 1,
      size: 200,
      pages: 1,
    });
    searchOrderableItems.mockResolvedValue([ITEM]);
    quoteOrder.mockResolvedValue({
      warehouse_id: "wh-1",
      currency: "INR",
      subtotal_minor: 0,
      total_minor: 0,
      fully_priced: true,
      fully_available: true,
      lines: [],
    });
    createOrder.mockReset();
  });

  it("holds Continue on the first step until a warehouse and a customer name or phone are set", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "Customer & warehouse" });
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("Choose a warehouse.")).toBeDefined();
    expect(screen.getByText("Enter the customer's name or phone number.")).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Line items" })).toBeNull();
  });

  it("shows the phone-lookup gap note only in phone mode", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "Customer & warehouse" });
    expect(screen.getByText("Looking a caller up isn't available yet")).toBeDefined();

    await user.click(screen.getByRole("radio", { name: /Manual/ }));
    expect(screen.queryByText("Looking a caller up isn't available yet")).toBeNull();
  });

  it("moves to line items on Continue, and Back returns to the first step", async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Pune Plant");
    await user.type(screen.getByLabelText(/Customer name/), "Rohan Malhotra");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Line items" })).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { name: "Customer & warehouse" })).toBeDefined();
    expect(screen.getByLabelText("Warehouse")).toHaveProperty("value", "wh-1");
  });

  it("creates the order from the line items step, with the picked mode's channel", async () => {
    const user = userEvent.setup();
    createOrder.mockResolvedValue(CREATED);
    renderPage();

    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.click(screen.getByRole("radio", { name: /Sales/ }));
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Pune Plant");
    await user.type(screen.getByLabelText(/Customer name/), "Rohan Malhotra");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "Line items" });
    await chooseItem(user);
    await user.type(screen.getByLabelText("Quantity"), "5");
    await user.click(screen.getByRole("button", { name: "Create order" }));

    await waitFor(() => expect(createOrder).toHaveBeenCalledTimes(1));
    const body = createOrder.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body).toEqual({
      channel: "sales",
      warehouse_id: "wh-1",
      customer_name: "Rohan Malhotra",
      lines: [{ inventory_item_id: "item-1", quantity: "5" }],
    });
  });

  it("holds Create on the line items step until every line has an item and a quantity", async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Pune Plant");
    await user.type(screen.getByLabelText(/Customer phone/), "+91 98200 11122");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "Line items" });
    await user.click(screen.getByRole("button", { name: "Create order" }));

    expect(await screen.findByText("Look up an item.")).toBeDefined();
    expect(createOrder).not.toHaveBeenCalled();
  });
});
