/**
 * Carriers (kind=carrier channels): connect with the courier account's own login, register the
 * tracking webhook, and the dispatch carrier picker. Every write is behind
 * `operations.channels.channel.write:ORG`; the credential is never read back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ChannelOut, OrderOut, Page, WarehouseOut } from "@/api/types";
import { DispatchOrderDialog } from "@/forms/DispatchOrderDialog";
import { renderScreen } from "@/test-utils/renderScreen";
import { CarriersPage } from "./Carriers";

const listChannels = vi.fn();
const listWarehouses = vi.fn();
const connectChannel = vi.fn();
const reconnectChannel = vi.fn();
const disconnectChannel = vi.fn();
const getWebhookSetup = vi.fn();
const listOrders = vi.fn();
const dispatchOrder = vi.fn();

vi.mock("@/api/client", () => ({
  listChannels: (...a: unknown[]) => listChannels(...a),
  listWarehouses: (...a: unknown[]) => listWarehouses(...a),
  connectChannel: (...a: unknown[]) => connectChannel(...a),
  reconnectChannel: (...a: unknown[]) => reconnectChannel(...a),
  disconnectChannel: (...a: unknown[]) => disconnectChannel(...a),
  getWebhookSetup: (...a: unknown[]) => getWebhookSetup(...a),
  listOrders: (...a: unknown[]) => listOrders(...a),
  dispatchOrder: (...a: unknown[]) => dispatchOrder(...a),
}));

const READ = "operations.channels.channel.read:ORG";
const WRITE = "operations.channels.channel.write:ORG";

const WAREHOUSES: Page<WarehouseOut> = {
  items: [{ id: "wh-1", code: "WH1", name: "Main warehouse", location: "Pune" }],
  total: 1,
  page: 1,
  size: 200,
  pages: 1,
};

function carrier(overrides: Partial<ChannelOut> = {}): ChannelOut {
  return {
    id: "car-1",
    provider: "shiprocket",
    kind: "carrier",
    name: "Shiprocket Pune",
    status: "connected",
    external_shop_id: "Primary",
    api_key_masked: "••••.com",
    default_warehouse_id: "wh-1",
    default_currency: "INR",
    auto_accept_orders: false,
    last_orders_synced_at: null,
    last_inventory_synced_at: null,
    last_health_check_at: null,
    consecutive_failures: 0,
    last_error_code: null,
    last_error_message: null,
    connected_at: "2026-10-01T00:00:00Z",
    disconnected_at: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

function stubViewport(wide: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

beforeEach(() => {
  for (const fn of [
    listChannels,
    listWarehouses,
    connectChannel,
    reconnectChannel,
    disconnectChannel,
    getWebhookSetup,
    listOrders,
    dispatchOrder,
  ]) {
    fn.mockReset();
  }
  listWarehouses.mockResolvedValue(WAREHOUSES);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CarriersPage", () => {
  it("asks only for carrier connections", async () => {
    listChannels.mockResolvedValue(page([carrier()]));
    renderScreen(<CarriersPage />, [READ]);

    expect(await screen.findByText("Shiprocket Pune")).toBeDefined();
    expect(listChannels).toHaveBeenCalledWith(expect.objectContaining({ kind: "carrier" }));
  });

  it("hides every write without the write grant", async () => {
    listChannels.mockResolvedValue(page([carrier()]));
    renderScreen(<CarriersPage />, [READ]);

    await screen.findByText("Shiprocket Pune");
    expect(screen.queryByRole("button", { name: "Connect courier" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Courier Shiprocket Pune/ })).toBeNull();
  });

  it("connects a courier with its login and pickup location", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([]));
    connectChannel.mockResolvedValue(carrier());
    renderScreen(<CarriersPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Connect courier" }));
    await user.type(screen.getByLabelText("Connection name"), "Shiprocket Pune");
    await user.type(screen.getByLabelText("Pickup location"), "Primary");
    await user.type(screen.getByLabelText("API user email"), "api@acme.test");
    await user.type(screen.getByLabelText("API user password"), "pw-123456");
    await user.selectOptions(screen.getByLabelText("Warehouse"), "wh-1");
    await user.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(connectChannel).toHaveBeenCalledWith({
        provider: "shiprocket",
        kind: "carrier",
        name: "Shiprocket Pune",
        external_shop_id: "Primary",
        api_key: "api@acme.test", // pragma: allowlist secret
        api_secret: "pw-123456", // pragma: allowlist secret
        default_warehouse_id: "wh-1",
        auto_accept_orders: false,
      }),
    );
  });

  it("connects Delhivery with its API token alone", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([]));
    connectChannel.mockResolvedValue(carrier({ provider: "delhivery" }));
    renderScreen(<CarriersPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Connect courier" }));
    await user.selectOptions(screen.getByLabelText("Courier"), "delhivery");
    expect(screen.queryByLabelText("API user password")).toBeNull();
    await user.type(screen.getByLabelText("Connection name"), "Delhivery Pune");
    await user.type(screen.getByLabelText("Pickup location"), "Pune WH");
    await user.type(screen.getByLabelText("API token"), "dl-token-1");
    await user.selectOptions(screen.getByLabelText("Warehouse"), "wh-1");
    await user.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(connectChannel).toHaveBeenCalledWith({
        provider: "delhivery",
        kind: "carrier",
        name: "Delhivery Pune",
        external_shop_id: "Pune WH",
        api_key: "dl-token-1", // pragma: allowlist secret
        default_warehouse_id: "wh-1",
        auto_accept_orders: false,
      }),
    );
  });

  it("reconnects Delhivery with a token only", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([carrier({ provider: "delhivery", status: "error" })]));
    reconnectChannel.mockResolvedValue(carrier({ provider: "delhivery" }));
    renderScreen(<CarriersPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: /Reconnect Courier Shiprocket Pune/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("API token"), "dl-new");
    await user.click(within(dialog).getByRole("button", { name: "Reconnect" }));

    await waitFor(() => expect(reconnectChannel).toHaveBeenCalledWith("car-1", { api_key: "dl-new" })); // pragma: allowlist secret
  });

  it("refuses to connect without a password, and never calls the server", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([]));
    renderScreen(<CarriersPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Connect courier" }));
    await user.type(screen.getByLabelText("Connection name"), "Shiprocket Pune");
    await user.type(screen.getByLabelText("Pickup location"), "Primary");
    await user.type(screen.getByLabelText("API user email"), "api@acme.test");
    await user.selectOptions(screen.getByLabelText("Warehouse"), "wh-1");
    await user.click(screen.getByRole("button", { name: "Connect" }));

    expect(await screen.findByText(/sealed at rest, never shown again/)).toBeDefined();
    expect(connectChannel).not.toHaveBeenCalled();
  });

  it("shows the webhook URL and token to register, fetched on open", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([carrier()]));
    getWebhookSetup.mockResolvedValue({
      path: "/api/v1/webhooks/operations/parcel-track?connection=car-1",
      token: "tok-xyz",
    });
    renderScreen(<CarriersPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: /Tracking webhook Courier Shiprocket Pune/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("tok-xyz")).toBeDefined();
    expect(within(dialog).getByText(/\/api\/v1\/webhooks\/operations\/parcel-track\?connection=car-1$/)).toBeDefined();
    expect(getWebhookSetup).toHaveBeenCalledWith("car-1");
  });

  it("reconnects with a fresh login", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([carrier({ status: "error", last_error_message: "credential rejected" })]));
    reconnectChannel.mockResolvedValue(carrier());
    renderScreen(<CarriersPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: /Reconnect Courier Shiprocket Pune/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("API user email"), "new@acme.test");
    await user.type(within(dialog).getByLabelText("API user password"), "new-pw-1");
    await user.click(within(dialog).getByRole("button", { name: "Reconnect" }));

    await waitFor(() =>
      expect(reconnectChannel).toHaveBeenCalledWith("car-1", {
        api_key: "new@acme.test", // pragma: allowlist secret
        api_secret: "new-pw-1", // pragma: allowlist secret
      }),
    );
  });

  it("is usable at 390px: the courier shows as a card with its actions reachable", async () => {
    stubViewport(false);
    listChannels.mockResolvedValue(page([carrier()]));
    renderScreen(<CarriersPage />, [READ, WRITE]);

    expect(await screen.findByText("Shiprocket Pune")).toBeDefined();
    expect(screen.getByRole("button", { name: "Connect courier" })).toBeDefined();
    expect(screen.getAllByRole("button", { name: /Courier Shiprocket Pune/ }).length).toBeGreaterThan(0);
  });
});

describe("DispatchOrderDialog carrier picker", () => {
  const READY = {
    id: "ord-1",
    number: "ORD-1",
    customer_name: "Asha",
    version: 4,
  } as unknown as OrderOut;

  it("books through a connected courier with the details in integer paise", async () => {
    const user = userEvent.setup();
    listOrders.mockResolvedValue(page([READY]));
    listChannels.mockResolvedValue(page([carrier()]));
    dispatchOrder.mockResolvedValue({ id: "ship-1" });
    renderScreen(<DispatchOrderDialog onClose={() => undefined} onDispatched={() => undefined} />, []);

    await user.selectOptions(await screen.findByLabelText("Order"), "ord-1");
    await user.selectOptions(await screen.findByLabelText("Carrier connection"), "car-1");
    expect(screen.queryByLabelText("Carrier")).toBeNull();
    await user.type(screen.getByLabelText("Recipient name"), "Asha Verma");
    await user.type(screen.getByLabelText("Recipient phone"), "9876543210");
    await user.type(screen.getByLabelText("Address"), "12 MG Road");
    await user.type(screen.getByLabelText("City"), "Bengaluru");
    await user.type(screen.getByLabelText("State"), "Karnataka");
    await user.type(screen.getByLabelText("Pincode"), "560001");
    await user.type(screen.getByLabelText("Weight (grams)"), "500");
    await user.type(screen.getByLabelText(/Cash to collect/), "1250.50");
    await user.click(screen.getByRole("button", { name: "Confirm handover" }));

    await waitFor(() =>
      expect(dispatchOrder).toHaveBeenCalledWith({
        order_id: "ord-1",
        order_version: 4,
        channel_id: "car-1",
        booking: {
          consignee_name: "Asha Verma",
          consignee_phone: "9876543210",
          consignee_address: "12 MG Road",
          consignee_city: "Bengaluru",
          consignee_state: "Karnataka",
          destination_pincode: "560001",
          weight_grams: 500,
          cod_amount_minor: 125050,
        },
      }),
    );
  });

  it("refuses half-filled booking details, and never calls the server", async () => {
    const user = userEvent.setup();
    listOrders.mockResolvedValue(page([READY]));
    listChannels.mockResolvedValue(page([carrier()]));
    renderScreen(<DispatchOrderDialog onClose={() => undefined} onDispatched={() => undefined} />, []);

    await user.selectOptions(await screen.findByLabelText("Order"), "ord-1");
    await user.selectOptions(await screen.findByLabelText("Carrier connection"), "car-1");
    await user.type(screen.getByLabelText("Recipient name"), "Asha Verma");
    await user.click(screen.getByRole("button", { name: "Confirm handover" }));

    expect(await screen.findByText(/Fill every booking detail/)).toBeDefined();
    expect(dispatchOrder).not.toHaveBeenCalled();
  });

  it("still takes a manually entered carrier when none is chosen", async () => {
    const user = userEvent.setup();
    listOrders.mockResolvedValue(page([READY]));
    listChannels.mockResolvedValue(page([]));
    dispatchOrder.mockResolvedValue({ id: "ship-1" });
    renderScreen(<DispatchOrderDialog onClose={() => undefined} onDispatched={() => undefined} />, []);

    await user.selectOptions(await screen.findByLabelText("Order"), "ord-1");
    await user.type(screen.getByLabelText("Carrier"), "Acme Couriers");
    await user.click(screen.getByRole("button", { name: "Confirm handover" }));

    await waitFor(() =>
      expect(dispatchOrder).toHaveBeenCalledWith({ order_id: "ord-1", order_version: 4, carrier_name: "Acme Couriers" }),
    );
  });
});
