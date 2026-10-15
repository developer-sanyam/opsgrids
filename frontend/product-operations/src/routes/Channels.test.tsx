/**
 * The channels list (Fulfilment F11): connects to the real backend, gates every write behind
 * `operations.channels.channel.write:ORG`, and a reconnect always needs a fresh API key.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ChannelOut, Page, WarehouseOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { ChannelsPage } from "./Channels";

const listChannels = vi.fn();
const listWarehouses = vi.fn();
const connectChannel = vi.fn();
const disconnectChannel = vi.fn();
const reconnectChannel = vi.fn();

vi.mock("@/api/client", () => ({
  listChannels: (...a: unknown[]) => listChannels(...a),
  listWarehouses: (...a: unknown[]) => listWarehouses(...a),
  connectChannel: (...a: unknown[]) => connectChannel(...a),
  disconnectChannel: (...a: unknown[]) => disconnectChannel(...a),
  reconnectChannel: (...a: unknown[]) => reconnectChannel(...a),
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

function channel(overrides: Partial<ChannelOut> = {}): ChannelOut {
  return {
    id: "chan-1",
    provider: "mock_marketplace",
    kind: "sales",
    name: "My sandbox shop",
    status: "connected",
    external_shop_id: "shop-1",
    api_key_masked: "••••1234",
    default_warehouse_id: "wh-1",
    default_currency: "INR",
    auto_accept_orders: true,
    last_orders_synced_at: null,
    last_inventory_synced_at: null,
    last_health_check_at: null,
    consecutive_failures: 0,
    last_error_code: null,
    last_error_message: null,
    connected_at: "2026-09-01T00:00:00Z",
    disconnected_at: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function page(items: ChannelOut[]): Page<ChannelOut> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

describe("ChannelsPage", () => {
  beforeEach(() => {
    listChannels.mockReset();
    listWarehouses.mockReset();
    connectChannel.mockReset();
    disconnectChannel.mockReset();
    reconnectChannel.mockReset();
    listWarehouses.mockResolvedValue(WAREHOUSES);
  });

  it("lists channels with their warehouse resolved from the id", async () => {
    listChannels.mockResolvedValue(page([channel()]));
    renderScreen(<ChannelsPage />, [READ]);

    expect(await screen.findByText("My sandbox shop")).toBeDefined();
    expect(screen.getByText("shop-1")).toBeDefined();
    expect(screen.getByText("WH1")).toBeDefined();
  });

  it("hides Connect channel and every row action without the write grant", async () => {
    listChannels.mockResolvedValue(page([channel()]));
    renderScreen(<ChannelsPage />, [READ]);

    await screen.findByText("My sandbox shop");
    expect(screen.queryByRole("button", { name: "Connect channel" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Disconnect Channel My sandbox shop/ })).toBeNull();
  });

  it("connects a channel with the values entered, then closes the dialog and reloads", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValueOnce(page([])).mockResolvedValueOnce(page([channel()]));
    connectChannel.mockResolvedValue(channel());
    renderScreen(<ChannelsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Connect channel" }));
    await user.type(screen.getByLabelText("Connection name"), "My sandbox shop");
    await user.type(screen.getByLabelText("Sandbox shop id"), "shop-1");
    await user.type(screen.getByLabelText("API key"), "sandbox-key-1");
    await user.selectOptions(screen.getByLabelText("Default warehouse"), "wh-1");
    await user.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(connectChannel).toHaveBeenCalledWith({
        provider: "mock_marketplace",
        name: "My sandbox shop",
        external_shop_id: "shop-1",
        api_key: "sandbox-key-1", // pragma: allowlist secret
        default_warehouse_id: "wh-1",
        auto_accept_orders: true,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: "Connect" })).toBeNull());
    expect(listChannels).toHaveBeenCalledTimes(2);
  });

  it("refuses to connect without a warehouse, and never calls the server", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([]));
    renderScreen(<ChannelsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Connect channel" }));
    await user.type(screen.getByLabelText("Connection name"), "My sandbox shop");
    await user.type(screen.getByLabelText("Sandbox shop id"), "shop-1");
    await user.type(screen.getByLabelText("API key"), "sandbox-key-1");
    await user.click(screen.getByRole("button", { name: "Connect" }));

    expect(await screen.findByText(/Choose where an imported order's stock is reserved/)).toBeDefined();
    expect(connectChannel).not.toHaveBeenCalled();
  });

  it("disconnects a channel after confirming", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([channel()]));
    disconnectChannel.mockResolvedValue(channel({ status: "disconnected" }));
    renderScreen(<ChannelsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: /Disconnect Channel My sandbox shop/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(disconnectChannel).toHaveBeenCalledWith("chan-1"));
  });

  it("refuses to reconnect with a blank key, and never calls the server", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([channel({ status: "error" })]));
    renderScreen(<ChannelsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: /Reconnect Channel My sandbox shop/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Reconnect" }));

    expect(await screen.findByText(/fresh API key is needed/)).toBeDefined();
    expect(reconnectChannel).not.toHaveBeenCalled();
  });

  it("reconnects with the freshly typed key", async () => {
    const user = userEvent.setup();
    listChannels.mockResolvedValue(page([channel({ status: "error" })]));
    reconnectChannel.mockResolvedValue(channel({ status: "connected" }));
    renderScreen(<ChannelsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: /Reconnect Channel My sandbox shop/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("New API key"), "rotated-key");
    await user.click(within(dialog).getByRole("button", { name: "Reconnect" }));

    await waitFor(() => expect(reconnectChannel).toHaveBeenCalledWith("chan-1", { api_key: "rotated-key" })); // pragma: allowlist secret
  });
});
