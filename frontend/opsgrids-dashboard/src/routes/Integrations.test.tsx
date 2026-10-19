import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { CONNECTORS } from "@/lib/integrations";
import { IntegrationsPage } from "./Integrations";

vi.mock("@/api/integrations", () => ({
  listChannels: vi.fn().mockResolvedValue([
    {
      id: "chan-1",
      provider: "shopify",
      kind: "sales",
      name: "My Shopify Store",
      status: "connected",
      external_shop_id: "demo-store.myshopify.com",
      api_key_masked: "shpat_...1234",
      default_warehouse_id: "wh-1",
      default_currency: "INR",
      auto_accept_orders: true,
      consecutive_failures: 0,
      connected_at: "2026-10-18T10:00:00Z",
      created_at: "2026-10-18T10:00:00Z",
      updated_at: "2026-10-18T10:00:00Z",
    },
  ]),
  getMerchantAccount: vi.fn().mockResolvedValue({
    id: "merch-1",
    provider: "razorpay",
    key_id: "rzp_live_testkey123",
    mode: "sandbox",
    webhook_registered: true,
    verified: true,
    last_verified_at: "2026-10-18T10:00:00Z",
    created_at: "2026-10-18T10:00:00Z",
    updated_at: "2026-10-18T10:00:00Z",
  }),
  listWarehouses: vi.fn().mockResolvedValue([
    { id: "wh-1", code: "WH-MUM", name: "Mumbai Central", active: true },
    { id: "wh-2", code: "WH-DEL", name: "Delhi Hub", active: true },
  ]),
  connectChannel: vi.fn(),
  updateChannelConfig: vi.fn(),
  reconnectChannel: vi.fn(),
  disconnectChannel: vi.fn(),
  syncChannelOrders: vi.fn(),
  syncChannelInventory: vi.fn(),
  checkChannelHealth: vi.fn(),
  saveMerchantAccount: vi.fn(),
  verifyMerchantAccount: vi.fn(),
  deleteMerchantAccount: vi.fn(),
}));

function arrange() {
  return render(
    <MemoryRouter initialEntries={["/integrations"]}>
      <IntegrationsPage />
    </MemoryRouter>,
  );
}

describe("IntegrationsPage", () => {
  it("renders the centralized integrations hub with categories and statistics", async () => {
    arrange();

    expect(screen.getByText("Connect & Automate Across Your Platform")).toBeTruthy();
    expect(screen.getByText("Active Integrations")).toBeTruthy();
    expect(screen.getByText("Available Connectors")).toBeTruthy();

    for (const name of [
      "Sales & Channels",
      "Couriers & Logistics",
      "Payment Gateways",
      "Messaging & Comms",
      "POS Systems",
      "ERP & Supply Chain",
    ]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeTruthy();
    }
  });

  it("displays live connection status for connected channels and merchant accounts", async () => {
    arrange();

    await waitFor(() => {
      // Shopify is connected in our mock
      expect(screen.getByText("demo-store.myshopify.com • Auto-accept ON")).toBeTruthy();
      // Razorpay is connected in our mock
      expect(screen.getByText(/rzp_live_testkey123/)).toBeTruthy();
    });

    const manageButtons = screen.getAllByRole("button", { name: "Manage" });
    expect(manageButtons.length).toBeGreaterThanOrEqual(2);
  });

  it("opens the Connect dialog directly on app.opsgrids when clicking Connect on an available integration", async () => {
    arrange();

    const connectButtons = screen.getAllByRole("button", { name: "Connect" });
    expect(connectButtons.length).toBeGreaterThan(0);

    // Click connect on ShipRocket
    const shiprocketCard = screen.getByRole("heading", { name: "ShipRocket" }).closest("li");
    expect(shiprocketCard).toBeTruthy();
    const connectShiprocket = shiprocketCard?.querySelector("button");
    expect(connectShiprocket).toBeTruthy();

    if (connectShiprocket) {
      await userEvent.click(connectShiprocket);
      expect(screen.getByText("Connect ShipRocket")).toBeTruthy();
      expect(screen.getByText("Registered pickup location")).toBeTruthy();
      expect(screen.getByText("API user email")).toBeTruthy();
    }
  });

  it("filters integrations by search and category pills", async () => {
    arrange();

    const search = screen.getByRole("searchbox", { name: "Search integrations" });
    await userEvent.type(search, "delhivery");

    expect(screen.getByRole("heading", { name: "Delhivery" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Shopify" })).toBeNull();

    await userEvent.clear(search);
    await userEvent.type(search, "nonexistent999");
    expect(screen.getByText("No integrations match that filter")).toBeTruthy();
  });
});
