/**
 * The new-purchase-order route: two steps (order details, then line items) behind the shared
 * `Stepper`. Continue on the first step validates what belongs to it and holds the user there on
 * a problem; the create only ever fires from the last step.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type { PurchaseOrderOut, SupplierOut } from "@/api/types";
import { PurchaseOrderFormPage } from "./PurchaseOrderForm";

const listWarehouses = vi.fn();
const searchActiveSuppliers = vi.fn();
const searchInventory = vi.fn();
const createPurchaseOrder = vi.fn();
const getPurchaseOrder = vi.fn();
const updatePurchaseOrder = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  searchActiveSuppliers: (...args: unknown[]) => searchActiveSuppliers(...args),
  searchInventory: (...args: unknown[]) => searchInventory(...args),
  createPurchaseOrder: (...args: unknown[]) => createPurchaseOrder(...args),
  getPurchaseOrder: (...args: unknown[]) => getPurchaseOrder(...args),
  updatePurchaseOrder: (...args: unknown[]) => updatePurchaseOrder(...args),
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

const SUPPLIER: SupplierOut = {
  id: "sup-1",
  code: "SUP-01",
  name: "Kaveri Textiles",
  category: "raw_materials",
  status: "active",
  lead_time_days: 7,
  on_time_pct: 95,
  spend_minor: 0,
  currency: "INR",
  open_pos: 0,
  contact_name: "Priya",
  contact_email: "priya@kaveritextiles.example",
  location: "Coimbatore",
  since: "2024-01-01",
};

const CREATED: PurchaseOrderOut = {
  id: "po-new",
  po_number: "PO-1001",
  supplier_id: "sup-1",
  supplier_name: "Kaveri Textiles",
  category: "raw_materials",
  status: "draft",
  currency: "INR",
  warehouse: "Bengaluru DC",
  raised_by: "Asha Rao",
  created_date: "2026-09-25",
  expected_date: "2026-10-01",
  received_date: null,
  line_items: [],
  notes: null,
};

function renderPage() {
  const router = createMemoryRouter(
    [
      { path: "/purchase-orders/new", element: <PurchaseOrderFormPage /> },
      { path: "/purchase-orders", element: <h1>Purchase orders</h1> },
      { path: "/purchase-orders/:id", element: <h1>Purchase order detail</h1> },
    ],
    { initialEntries: ["/purchase-orders/new"] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <RouterProvider router={router} />
    </AuthContext.Provider>,
  );
  return router;
}

async function chooseSupplier(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("combobox", { name: "Supplier" }), "Kave");
  await waitFor(() => expect(screen.getByRole("option", { name: /Kaveri Textiles/ })).toBeDefined());
  await user.keyboard("{ArrowDown}{Enter}");
}

describe("PurchaseOrderFormPage", () => {
  beforeEach(() => {
    listWarehouses.mockResolvedValue({ items: [{ name: "Bengaluru DC" }], total: 1, page: 1, size: 200 });
    searchActiveSuppliers.mockResolvedValue([SUPPLIER]);
    searchInventory.mockResolvedValue([]);
    createPurchaseOrder.mockReset();
    getPurchaseOrder.mockReset();
    updatePurchaseOrder.mockReset();
  });

  it("holds Continue on the details step until the required fields are set", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "Order details" });
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("Choose a supplier.")).toBeDefined();
    expect(screen.getByText("Choose a warehouse.")).toBeDefined();
    expect(screen.getByText("Pick an expected delivery date.")).toBeDefined();
    expect(screen.getByRole("heading", { name: "Order details" })).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Line items" })).toBeNull();
  });

  it("moves to line items on Continue, and Back returns to order details", async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await chooseSupplier(user);
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Bengaluru DC");
    await user.type(screen.getByLabelText("Expected date"), "2026-10-01");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Line items" })).toBeDefined();
    expect(screen.queryByRole("heading", { name: "Order details" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { name: "Order details" })).toBeDefined();
    expect(screen.getByLabelText("Warehouse")).toHaveProperty("value", "Bengaluru DC");
  });

  it("creates the purchase order from the line items step", async () => {
    const user = userEvent.setup();
    createPurchaseOrder.mockResolvedValue(CREATED);
    renderPage();

    await waitFor(() => expect(listWarehouses).toHaveBeenCalled());
    await chooseSupplier(user);
    await user.selectOptions(await screen.findByLabelText("Warehouse"), "Bengaluru DC");
    await user.type(screen.getByLabelText("Expected date"), "2026-10-01");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "Line items" });
    await user.type(screen.getByLabelText("SKU (free text for a non-stock line)"), "RM-100");
    await user.type(screen.getByLabelText("Quantity"), "10");
    await user.type(screen.getByLabelText("Unit price"), "250");
    await user.click(screen.getByRole("button", { name: "Create purchase order" }));

    await waitFor(() => expect(createPurchaseOrder).toHaveBeenCalledTimes(1));
    const body = createPurchaseOrder.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body.warehouse).toBe("Bengaluru DC");
    expect(body.supplier_id).toBe("sup-1");
    expect(body.lines).toEqual([
      { sku: "RM-100", description: "", quantity: 10, unit_price_minor: 25000, uom: "" },
    ]);
  });
});
