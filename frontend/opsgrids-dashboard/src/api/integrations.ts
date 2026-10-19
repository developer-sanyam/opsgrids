/**
 * API client for managing integrations directly from the organization dashboard (app.opsgrids.com).
 * Covers Sales Channels, Couriers, and Payment Gateways.
 */
import { ApiError, apiRequest } from "@opsgrids/client";

export type ChannelKind = "sales" | "erp" | "pos" | "carrier";
export type ChannelStatus = "connected" | "disconnected" | "error";

export interface ChannelOut {
  id: string;
  provider: string;
  kind: ChannelKind;
  name: string;
  status: ChannelStatus;
  external_shop_id: string;
  api_key_masked: string;
  default_warehouse_id: string | null;
  default_currency: string;
  auto_accept_orders: boolean;
  last_orders_synced_at: string | null;
  last_inventory_synced_at: string | null;
  last_health_check_at: string | null;
  consecutive_failures: number;
  last_error_code: string | null;
  last_error_message: string | null;
  connected_at: string | null;
  disconnected_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChannelConnectIn {
  provider: string;
  kind?: ChannelKind | undefined;
  name: string;
  external_shop_id: string;
  api_key: string;
  api_secret?: string | undefined;
  webhook_secret?: string | undefined;
  default_warehouse_id: string;
  default_currency?: string | undefined;
  auto_accept_orders?: boolean | undefined;
}

export interface ChannelConfigPatch {
  name?: string | undefined;
  default_warehouse_id?: string | undefined;
  auto_accept_orders?: boolean | undefined;
}

export interface ChannelReconnectIn {
  api_key: string;
  api_secret?: string | undefined;
  webhook_secret?: string | undefined;
}

export interface ChannelSyncResultOut {
  channel_id: string;
  sync_type: string;
  status: "success" | "failed";
  items_processed: number;
  error_message: string | null;
}

export interface ChannelHealthCheckOut {
  channel_id: string;
  healthy: boolean;
  status: ChannelStatus;
  detail: string;
}

export interface WarehouseOut {
  id: string;
  code: string;
  name: string;
  active: boolean;
}

export type CollectionsProvider = "razorpay" | "cashfree" | "phonepe" | "payu" | "paytm" | "stripe";

export interface MerchantAccountIn {
  provider: CollectionsProvider;
  key_id: string;
  key_secret: string;
  webhook_secret?: string | undefined;
  mode: "sandbox" | "production";
  config?: Record<string, string | number | boolean> | undefined;
}

export interface MerchantAccountOut {
  id: string;
  provider: CollectionsProvider;
  key_id: string;
  mode: "sandbox" | "production";
  webhook_registered: boolean;
  verified: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
}

/** `GET /operations/channels` */
export async function listChannels(params?: { status?: string; kind?: string }): Promise<ChannelOut[]> {
  try {
    const q = new URLSearchParams();
    if (params?.status) q.set("channel_status", params.status);
    if (params?.kind) q.set("kind", params.kind);
    const qs = q.toString() ? `?${q.toString()}` : "";
    return await apiRequest<ChannelOut[]>(`/operations/channels${qs}`);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 402 || err.status === 404)) {
      return [];
    }
    throw err;
  }
}

/** `POST /operations/channels` */
export function connectChannel(input: ChannelConnectIn): Promise<ChannelOut> {
  return apiRequest<ChannelOut>("/operations/channels", {
    method: "POST",
    body: input,
  });
}

/** `PATCH /operations/channels/{id}` */
export function updateChannelConfig(id: string, patch: ChannelConfigPatch): Promise<ChannelOut> {
  return apiRequest<ChannelOut>(`/operations/channels/${id}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `POST /operations/channels/{id}/reconnect` */
export function reconnectChannel(id: string, input: ChannelReconnectIn): Promise<ChannelOut> {
  return apiRequest<ChannelOut>(`/operations/channels/${id}/reconnect`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/channels/{id}/disconnect` */
export function disconnectChannel(id: string): Promise<ChannelOut> {
  return apiRequest<ChannelOut>(`/operations/channels/${id}/disconnect`, {
    method: "POST",
  });
}

/** `POST /operations/channels/{id}/sync/orders` */
export function syncChannelOrders(id: string): Promise<ChannelSyncResultOut> {
  return apiRequest<ChannelSyncResultOut>(`/operations/channels/${id}/sync/orders`, {
    method: "POST",
  });
}

/** `POST /operations/channels/{id}/sync/inventory` */
export function syncChannelInventory(id: string): Promise<ChannelSyncResultOut> {
  return apiRequest<ChannelSyncResultOut>(`/operations/channels/${id}/sync/inventory`, {
    method: "POST",
  });
}

/** `POST /operations/channels/{id}/health-check` */
export function checkChannelHealth(id: string): Promise<ChannelHealthCheckOut> {
  return apiRequest<ChannelHealthCheckOut>(`/operations/channels/${id}/health-check`, {
    method: "POST",
  });
}

/** `GET /operations/warehouses` */
export async function listWarehouses(params?: { size?: number }): Promise<WarehouseOut[]> {
  try {
    const size = params?.size ?? 200;
    const res = await apiRequest<{ items: WarehouseOut[] }>(`/operations/warehouses?size=${size}`);
    return res.items ?? [];
  } catch (err) {
    if (err instanceof ApiError && (err.status === 402 || err.status === 404)) {
      return [];
    }
    throw err;
  }
}

/** `GET /payment/merchant-account` */
export async function getMerchantAccount(): Promise<MerchantAccountOut | null> {
  try {
    return await apiRequest<MerchantAccountOut>("/payment/merchant-account");
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 402)) {
      return null;
    }
    throw err;
  }
}

/** `PUT /payment/merchant-account` */
export function saveMerchantAccount(input: MerchantAccountIn): Promise<MerchantAccountOut> {
  return apiRequest<MerchantAccountOut>("/payment/merchant-account", {
    method: "PUT",
    body: input,
  });
}

/** `POST /payment/merchant-account/verify` */
export function verifyMerchantAccount(): Promise<MerchantAccountOut> {
  return apiRequest<MerchantAccountOut>("/payment/merchant-account/verify", {
    method: "POST",
  });
}

/** `DELETE /payment/merchant-account` */
export function deleteMerchantAccount(reason?: string): Promise<void> {
  const qs = reason ? `?reason=${encodeURIComponent(reason)}` : "";
  return apiRequest<void>(`/payment/merchant-account${qs}`, {
    method: "DELETE",
  });
}
