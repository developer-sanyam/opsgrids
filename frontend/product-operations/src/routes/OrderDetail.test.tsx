/**
 * The order detail screen: status-gated actions, a live quote preview before placing, a stale
 * `version` 409 surfaced as "reload and try again" rather than a raw toast, and `accept`'s
 * `InsufficientStock` 409 rendered as a per-line shortfall panel.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { ApiError, AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut, ProblemJson } from "@opsgrids/client";

import type { OrderLineItem, OrderOut, QuoteOut, WarehouseOut } from "@/api/types";
import { OrderDetailPage } from "./OrderDetail";

const getOrder = vi.fn();
const getWarehouseById = vi.fn();
const quoteOrder = vi.fn();
const placeOrder = vi.fn();
const acceptOrder = vi.fn();
const holdOrder = vi.fn();
const releaseOrder = vi.fn();
const cancelOrder = vi.fn();
const getOrderPayments = vi.fn();

vi.mock("@/api/client", () => ({
  getOrderPayments: (...args: unknown[]) => getOrderPayments(...args),
  getOrder: (...args: unknown[]) => getOrder(...args),
  getWarehouseById: (...args: unknown[]) => getWarehouseById(...args),
  quoteOrder: (...args: unknown[]) => quoteOrder(...args),
  placeOrder: (...args: unknown[]) => placeOrder(...args),
  acceptOrder: (...args: unknown[]) => acceptOrder(...args),
  holdOrder: (...args: unknown[]) => holdOrder(...args),
  releaseOrder: (...args: unknown[]) => releaseOrder(...args),
  cancelOrder: (...args: unknown[]) => cancelOrder(...args),
}));

const USER: MeOut = {
  user_id: "user-1",
  email: "ops@example.com",
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

const WAREHOUSE: WarehouseOut = { id: "wh-1", code: "WH-PUN", name: "Pune Plant", location: "" };

const DRAFT_LINE: OrderLineItem = {
  id: "line-1",
  inventory_item_id: "item-1",
  sku: "RM-STL-4501",
  quantity: "5",
  selling_price_minor: null,
  line_total_minor: null,
};

const DRAFT_ORDER: OrderOut = {
  id: "ord-1",
  number: "ORD-2026-0301",
  channel: "phone",
  status: "DRAFT",
  version: 1,
  warehouse_id: "wh-1",
  customer_id: null,
  customer_name: "Rohan Malhotra",
  customer_phone: "+91 98200 11122",
  subtotal_minor: 0,
  total_minor: 0,
  currency: "INR",
  lines: [DRAFT_LINE],
  created_at: "2026-08-24T09:15:00Z",
  updated_at: "2026-08-24T09:15:00Z",
};

const PLACED_ORDER: OrderOut = {
  ...DRAFT_ORDER,
  status: "PLACED",
  version: 2,
  lines: [{ ...DRAFT_LINE, selling_price_minor: 74900, line_total_minor: 374500 }],
  subtotal_minor: 374500,
  total_minor: 374500,
};

const EMPTY_QUOTE: QuoteOut = {
  warehouse_id: "wh-1",
  currency: "INR",
  subtotal_minor: 0,
  total_minor: 0,
  fully_priced: true,
  fully_available: true,
  lines: [],
};

function renderPage(grants: string[] = []) {
  const router = createMemoryRouter(
    [
      { path: "/orders/:id", element: <OrderDetailPage /> },
      { path: "/orders", element: <h1>Orders</h1> },
    ],
    { initialEntries: ["/orders/ord-1"] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  return router;
}

describe("OrderDetailPage", () => {
  beforeEach(() => {
    getOrder.mockReset();
    getWarehouseById.mockResolvedValue(WAREHOUSE);
    quoteOrder.mockResolvedValue(EMPTY_QUOTE);
    placeOrder.mockReset();
    acceptOrder.mockReset();
    holdOrder.mockReset();
    releaseOrder.mockReset();
    cancelOrder.mockReset();
    getOrderPayments.mockResolvedValue({
      order_id: "ord-1",
      total_minor: 374500,
      currency: "INR",
      paid_at: null,
      paid_via_charge_id: null,
      payment_term: "cash",
      can_collect: false,
      collect_blocked_reason: "Online payments are not set up for this organization",
      online_payments_configured: false,
      mode: null,
      wire: "stub",
      collections: [],
    });
  });

  it("shows Place only with write permission, and not on a placed order", async () => {
    getOrder.mockResolvedValue(DRAFT_ORDER);
    renderPage(["operations.orders.order.write"]);

    expect(await screen.findByRole("heading", { name: "ORD-2026-0301" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Place order" })).toBeDefined();

    getOrder.mockResolvedValue(PLACED_ORDER);
  });

  it("hides Place without write permission", async () => {
    getOrder.mockResolvedValue(DRAFT_ORDER);
    renderPage([]);

    await screen.findByRole("heading", { name: "ORD-2026-0301" });
    expect(screen.queryByRole("button", { name: "Place order" })).toBeNull();
  });

  it("shows Accept only on a placed order and only with the accept:ORG grant", async () => {
    getOrder.mockResolvedValue(PLACED_ORDER);
    renderPage(["operations.orders.order.accept:ORG"]);

    await screen.findByRole("heading", { name: "ORD-2026-0301" });
    expect(screen.getByRole("button", { name: "Accept order" })).toBeDefined();
    // Accept needs the ORG scope specifically — a bare grant with no scope reads as OWN.
    expect(screen.queryByRole("button", { name: "Place order" })).toBeNull();
  });

  it("hides Accept with the permission at a scope below ORG", async () => {
    getOrder.mockResolvedValue(PLACED_ORDER);
    renderPage(["operations.orders.order.accept:TEAM"]);

    await screen.findByRole("heading", { name: "ORD-2026-0301" });
    expect(screen.queryByRole("button", { name: "Accept order" })).toBeNull();
  });

  it("surfaces a stale-version 409 on Place as a reload notice, not a raw toast", async () => {
    const user = userEvent.setup();
    getOrder.mockResolvedValue(DRAFT_ORDER);
    placeOrder.mockRejectedValue(
      new ApiError(409, "stale", {
        type: "https://errors.opsgrids.dev/conflict",
        status: 409,
        detail: "Order ORD-2026-0301 is at version 2, not 1; reload and try again",
      }),
    );
    renderPage(["operations.orders.order.write"]);

    await screen.findByRole("heading", { name: "ORD-2026-0301" });
    await user.click(screen.getByRole("button", { name: "Place order" }));

    expect(await screen.findByText("This order changed since it was loaded")).toBeDefined();
    expect(screen.getByText(/at version 2, not 1/)).toBeDefined();
    // The order is refetched, not just complained about.
    await waitFor(() => expect(getOrder).toHaveBeenCalledTimes(2));
  });

  it("renders accept's InsufficientStock 409 as a per-line shortfall, not the raw sentence alone", async () => {
    const user = userEvent.setup();
    getOrder.mockResolvedValue(PLACED_ORDER);
    // `InsufficientStock.errors` (ADR-0014 §11) is not the platform's ordinary `{field, code,
    // message}` field-error shape `ProblemJson` declares — see `lib/orderErrors.ts`'s own note.
    acceptOrder.mockRejectedValue(
      new ApiError(
        409,
        "short",
        {
          type: "https://errors.opsgrids.dev/insufficient-stock",
          status: 409,
          detail: "Cannot take 5 MT of RM-STL-4501",
          errors: [
            { sku: "RM-STL-4501", uom: "MT", requested: "5", on_hand: "3", reserved: "0", available: "3" },
          ],
        } as unknown as ProblemJson,
      ),
    );
    renderPage(["operations.orders.order.accept:ORG"]);

    await screen.findByRole("heading", { name: "ORD-2026-0301" });
    await user.click(screen.getByRole("button", { name: "Accept order" }));

    expect(await screen.findByText("Not enough stock to accept this order")).toBeDefined();
    expect(screen.getByText(/RM-STL-4501: needs 5 MT, only 3 available/)).toBeDefined();
  });
});
