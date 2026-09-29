/**
 * A channel's own sync history and SKU mappings (Fulfilment F11): every action drives the real
 * connector, and `operations.channels.channel.sync`/`.write` gate the actions independently.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ChannelOut, ChannelSkuMappingOut, ChannelSyncLogOut, Page } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { ChannelDetailDrawer } from "./ChannelDetailDrawer";

const checkChannelHealth = vi.fn();
const deleteSkuMapping = vi.fn();
const listSkuMappings = vi.fn();
const listSyncLogs = vi.fn();
const retryChannelSyncLog = vi.fn();
const syncChannelInventoryNow = vi.fn();
const syncChannelOrdersNow = vi.fn();
const upsertSkuMapping = vi.fn();

vi.mock("@/api/client", () => ({
  checkChannelHealth: (...a: unknown[]) => checkChannelHealth(...a),
  deleteSkuMapping: (...a: unknown[]) => deleteSkuMapping(...a),
  listSkuMappings: (...a: unknown[]) => listSkuMappings(...a),
  listSyncLogs: (...a: unknown[]) => listSyncLogs(...a),
  retryChannelSyncLog: (...a: unknown[]) => retryChannelSyncLog(...a),
  syncChannelInventoryNow: (...a: unknown[]) => syncChannelInventoryNow(...a),
  syncChannelOrdersNow: (...a: unknown[]) => syncChannelOrdersNow(...a),
  upsertSkuMapping: (...a: unknown[]) => upsertSkuMapping(...a),
  // SkuPicker's own dependency — never exercised by these tests, which don't type into it.
  searchInventory: vi.fn().mockResolvedValue([]),
}));

const SYNC = "operations.channels.channel.sync:ORG";
const WRITE = "operations.channels.channel.write:ORG";

const CHANNEL: ChannelOut = {
  id: "chan-1",
  provider: "mock_marketplace",
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
};

const EMPTY_MAPPINGS: Page<ChannelSkuMappingOut> = { items: [], total: 0, page: 1, size: 200, pages: 1 };
const EMPTY_LOGS: Page<ChannelSyncLogOut> = { items: [], total: 0, page: 1, size: 50, pages: 1 };

function mapping(overrides: Partial<ChannelSkuMappingOut> = {}): ChannelSkuMappingOut {
  return {
    id: "map-1",
    channel_id: "chan-1",
    external_sku: "EXT-SKU-1",
    inventory_item_id: "item-1",
    last_pushed_available_milli: null,
    last_pushed_at: null,
    last_push_status: null,
    last_push_error: null,
    ...overrides,
  };
}

function log(overrides: Partial<ChannelSyncLogOut> = {}): ChannelSyncLogOut {
  return {
    id: "log-1",
    channel_id: "chan-1",
    sync_type: "order_import",
    status: "failed",
    external_ref: "",
    detail: null,
    error_code: "timeout",
    error_message: "The provider did not respond in time",
    duration_ms: 1200,
    attempted_at: "2026-09-29T10:00:00Z",
    ...overrides,
  };
}

describe("ChannelDetailDrawer", () => {
  beforeEach(() => {
    checkChannelHealth.mockReset();
    deleteSkuMapping.mockReset();
    listSkuMappings.mockReset();
    listSyncLogs.mockReset();
    retryChannelSyncLog.mockReset();
    syncChannelInventoryNow.mockReset();
    syncChannelOrdersNow.mockReset();
    upsertSkuMapping.mockReset();
    listSkuMappings.mockResolvedValue(EMPTY_MAPPINGS);
    listSyncLogs.mockResolvedValue(EMPTY_LOGS);
  });

  it("hides every sync action without the sync grant", async () => {
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, []);
    await screen.findByText("No sync attempts yet");
    expect(screen.queryByRole("button", { name: "Sync orders now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sync inventory now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Check connection" })).toBeNull();
  });

  it("syncs orders now and shows the real counts back", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    syncChannelOrdersNow.mockResolvedValue({ attempted: 5, succeeded: 4, rejected: 1 });
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={onChanged} />, [SYNC]);

    await user.click(await screen.findByRole("button", { name: "Sync orders now" }));

    expect(await screen.findByText("Orders: 4 imported, 1 rejected, 5 attempted")).toBeDefined();
    expect(syncChannelOrdersNow).toHaveBeenCalledWith("chan-1");
    expect(onChanged).toHaveBeenCalled();
  });

  it("syncs inventory now and shows the pushed count", async () => {
    const user = userEvent.setup();
    syncChannelInventoryNow.mockResolvedValue({ attempted: 3, succeeded: 3, rejected: 0 });
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, [SYNC]);

    await user.click(await screen.findByRole("button", { name: "Sync inventory now" }));

    expect(await screen.findByText("Inventory: 3 of 3 SKUs pushed")).toBeDefined();
    expect(syncChannelInventoryNow).toHaveBeenCalledWith("chan-1");
  });

  it("checks the connection and reports an unhealthy result honestly", async () => {
    const user = userEvent.setup();
    checkChannelHealth.mockResolvedValue({ healthy: false });
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, [SYNC]);

    await user.click(await screen.findByRole("button", { name: "Check connection" }));

    expect(await screen.findByText("Connection reported unhealthy")).toBeDefined();
    expect(checkChannelHealth).toHaveBeenCalledWith("chan-1");
  });

  it("lists SKU mappings and their last-push failure", async () => {
    listSkuMappings.mockResolvedValue({
      items: [mapping({ last_push_status: "failed", last_push_error: "SKU not found on shop" })],
      total: 1,
      page: 1,
      size: 200,
      pages: 1,
    });
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, []);

    expect(await screen.findByText("EXT-SKU-1")).toBeDefined();
    expect(screen.getByText("SKU not found on shop")).toBeDefined();
  });

  it("refuses to map a SKU with nothing picked, and never calls the server", async () => {
    const user = userEvent.setup();
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, [WRITE]);

    await user.click(await screen.findByRole("button", { name: "Map SKU" }));

    expect(await screen.findByText(/Look up an inventory item to map this SKU to/)).toBeDefined();
    expect(upsertSkuMapping).not.toHaveBeenCalled();
  });

  it("removes a mapping", async () => {
    const user = userEvent.setup();
    listSkuMappings.mockResolvedValue({ items: [mapping()], total: 1, page: 1, size: 200, pages: 1 });
    deleteSkuMapping.mockResolvedValue(undefined);
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, [WRITE]);

    await user.click(await screen.findByRole("button", { name: /Remove mapping Mapping for EXT-SKU-1/ }));

    await waitFor(() => expect(deleteSkuMapping).toHaveBeenCalledWith("chan-1", "map-1"));
  });

  it("does not offer to remove a mapping without the write grant", async () => {
    listSkuMappings.mockResolvedValue({ items: [mapping()], total: 1, page: 1, size: 200, pages: 1 });
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, []);

    await screen.findByText("EXT-SKU-1");
    expect(screen.queryByRole("button", { name: /Remove mapping/ })).toBeNull();
  });

  it("retries a failed sync log entry", async () => {
    const user = userEvent.setup();
    listSyncLogs.mockResolvedValue({ items: [log()], total: 1, page: 1, size: 50, pages: 1 });
    retryChannelSyncLog.mockResolvedValue({ succeeded: true });
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, [SYNC]);

    expect(await screen.findByText("The provider did not respond in time")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /Retry Sync attempt order_import/ }));

    await waitFor(() => expect(retryChannelSyncLog).toHaveBeenCalledWith("chan-1", "log-1"));
  });

  it("does not offer a retry for a sync that already succeeded", async () => {
    listSyncLogs.mockResolvedValue({
      items: [log({ status: "success", error_code: null, error_message: null })],
      total: 1,
      page: 1,
      size: 50,
      pages: 1,
    });
    renderScreen(<ChannelDetailDrawer channel={CHANNEL} onClose={vi.fn()} onChanged={vi.fn()} />, [SYNC]);

    await screen.findByText("order import");
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
  });
});
