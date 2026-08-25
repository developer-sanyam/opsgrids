/**
 * Typed ITSM API client.
 *
 * Live by default: every function calls the itsm-service through the gateway at the same-origin
 * `/api/v1/itsm/...` prefix (Vite proxies it to `http://localhost:8100` in dev) with the bearer
 * token from the session. Set `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts`
 * with no login — handy for offline design work.
 */

import { apiRequest } from "./http";
import { ASSETS, CATALOG, INCIDENTS, REQUESTS } from "./fixtures";
import type {
  Asset,
  AssetStatus,
  AssetType,
  CatalogItem,
  Incident,
  IncidentStatus,
  Page,
  Priority,
  RequestStatus,
  ServiceRequest,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/**
 * The trend history and KPI deltas on the Overview are representative until the reporting service
 * ships; the UI marks those surfaces with a "Sample data" chip when running live. In fixtures mode
 * the whole app is illustrative, so nothing is singled out.
 */
export const TRENDS_ARE_SAMPLE = LIVE;

/** A generous page size — one request returns the whole (small) demo dataset for this org. */
const FULL_PAGE = 200;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* ------------------------------- incidents ------------------------------- */

export type IncidentSort = "updated" | "priority" | "created";

export interface ListIncidentsParams {
  search?: string;
  priority?: Priority | "all";
  status?: IncidentStatus | "all";
  sort?: IncidentSort;
}

const PRIORITY_RANK: Record<Priority, number> = { P1: 0, P2: 1, P3: 2, P4: 3 };

function sortIncidents(items: Incident[], sort: IncidentSort): Incident[] {
  const copy = [...items];
  if (sort === "priority") {
    copy.sort(
      (a, b) =>
        PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] ||
        b.updated_at.localeCompare(a.updated_at),
    );
  } else if (sort === "created") {
    copy.sort((a, b) => b.created_at.localeCompare(a.created_at));
  } else {
    copy.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }
  return copy;
}

export async function listIncidents(
  params: ListIncidentsParams = {},
): Promise<Page<Incident>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = INCIDENTS.filter((i) => {
      const matchesQuery =
        q === "" ||
        i.id.toLowerCase().includes(q) ||
        i.summary.toLowerCase().includes(q) ||
        i.assignee.toLowerCase().includes(q) ||
        (i.affected_asset_tag?.toLowerCase().includes(q) ?? false);
      const matchesPriority =
        !params.priority || params.priority === "all" || i.priority === params.priority;
      const matchesStatus =
        !params.status || params.status === "all" || i.status === params.status;
      return matchesQuery && matchesPriority && matchesStatus;
    });
    const sorted = sortIncidents(filtered, params.sort ?? "updated");
    return settle({ items: sorted, total: sorted.length });
  }

  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.priority && params.priority !== "all") query.set("priority", params.priority);
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.sort) query.set("sort", params.sort);
  return apiRequest<Page<Incident>>(`/itsm/incidents?${query.toString()}`);
}

/** Every incident for this org (unfiltered) — powers every Overview rollup. */
export async function getAllIncidents(): Promise<Incident[]> {
  if (USE_FIXTURES) return settle(INCIDENTS);
  const page = await apiRequest<Page<Incident>>(`/itsm/incidents?size=${FULL_PAGE}`);
  return page.items;
}

export async function getIncident(id: string): Promise<Incident> {
  if (USE_FIXTURES) {
    const found = INCIDENTS.find((i) => i.id === id);
    if (!found) throw new Error(`Incident ${id} not found`);
    return settle(found);
  }
  return apiRequest<Incident>(`/itsm/incidents/${encodeURIComponent(id)}`);
}

/* --------------------------- service requests --------------------------- */

export interface ListRequestsParams {
  search?: string;
  status?: RequestStatus | "all";
}

export async function listRequests(
  params: ListRequestsParams = {},
): Promise<Page<ServiceRequest>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = REQUESTS.filter((r) => {
      const matchesQuery =
        q === "" ||
        r.id.toLowerCase().includes(q) ||
        r.requester.toLowerCase().includes(q) ||
        r.subject.toLowerCase().includes(q) ||
        r.catalog_item_name.toLowerCase().includes(q);
      const matchesStatus =
        !params.status || params.status === "all" || r.status === params.status;
      return matchesQuery && matchesStatus;
    });
    return settle({ items: filtered, total: filtered.length });
  }

  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  return apiRequest<Page<ServiceRequest>>(`/itsm/service-requests?${query.toString()}`);
}

export async function getAllRequests(): Promise<ServiceRequest[]> {
  if (USE_FIXTURES) return settle(REQUESTS);
  const page = await apiRequest<Page<ServiceRequest>>(`/itsm/service-requests?size=${FULL_PAGE}`);
  return page.items;
}

export async function listCatalog(): Promise<CatalogItem[]> {
  if (USE_FIXTURES) return settle(CATALOG);
  const page = await apiRequest<Page<CatalogItem>>(`/itsm/catalog?size=${FULL_PAGE}`);
  return page.items;
}

/* -------------------------------- assets -------------------------------- */

export interface ListAssetsParams {
  search?: string;
  type?: AssetType | "all";
  status?: AssetStatus | "all";
}

export async function listAssets(params: ListAssetsParams = {}): Promise<Page<Asset>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = ASSETS.filter((a) => {
      const matchesQuery =
        q === "" ||
        a.tag.toLowerCase().includes(q) ||
        a.name.toLowerCase().includes(q) ||
        (a.owner?.toLowerCase().includes(q) ?? false) ||
        a.serial.toLowerCase().includes(q);
      const matchesType = !params.type || params.type === "all" || a.type === params.type;
      const matchesStatus =
        !params.status || params.status === "all" || a.status === params.status;
      return matchesQuery && matchesType && matchesStatus;
    });
    return settle({ items: filtered, total: filtered.length });
  }

  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.type && params.type !== "all") query.set("type", params.type);
  if (params.status && params.status !== "all") query.set("status", params.status);
  return apiRequest<Page<Asset>>(`/itsm/assets?${query.toString()}`);
}

export async function getAllAssets(): Promise<Asset[]> {
  if (USE_FIXTURES) return settle(ASSETS);
  const page = await apiRequest<Page<Asset>>(`/itsm/assets?size=${FULL_PAGE}`);
  return page.items;
}

export async function getAsset(tag: string): Promise<Asset> {
  if (USE_FIXTURES) {
    const found = ASSETS.find((a) => a.tag === tag);
    if (!found) throw new Error(`Asset ${tag} not found`);
    return settle(found);
  }
  return apiRequest<Asset>(`/itsm/assets/${encodeURIComponent(tag)}`);
}
