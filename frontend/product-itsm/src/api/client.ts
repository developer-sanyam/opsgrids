/**
 * Typed ITSM API client.
 *
 * Live by default: every function calls the itsm-service through the gateway at the same-origin
 * `/api/v1/itsm/...` prefix (Vite proxies it to the gateway in dev) with the bearer token from
 * the session. Set `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts` with no
 * login — handy for offline design work. Every function mirrors `services/itsm/app/api/routes.py`
 * and `app/schemas.py` field-for-field: no request shape is invented here that the route does not
 * accept, and no response field is read that the route does not return.
 */

import { ApiError, apiRequest, paginateFixture as paginate, settle } from "@opsgrids/client";
import type {
  Asset,
  AssetIn,
  AssetStatus,
  AssetType,
  AssetUpdate,
  CatalogItem,
  CatalogItemIn,
  CatalogItemUpdate,
  Incident,
  IncidentCategory,
  IncidentIn,
  IncidentStatus,
  IncidentStatusIn,
  IncidentUpdate,
  Overview,
  Page,
  Priority,
  RequestDecisionIn,
  RequestPatch,
  RequestStatus,
  ServiceRequest,
  ServiceRequestIn,
  WorklogEntry,
  WorklogIn,
} from "./types";

/**
 * `fixtures.ts` is ~1000 lines of seeded demo data that a live build must never ship. A static
 * `import` at the top of this module would pull it into every build regardless of which branch
 * runs; a dynamic `import()` behind the same `USE_FIXTURES` check that gates every call below
 * lets the bundler drop it entirely once `import.meta.env.DEV` is replaced with `false` in
 * production, since the branch that would import it is then dead code.
 */
let fixturesPromise: ReturnType<typeof loadFixtures> | null = null;
function loadFixtures() {
  return import("./fixtures");
}
function fixtures() {
  fixturesPromise ??= loadFixtures();
  return fixturesPromise;
}

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

const DEFAULT_PAGE = 1;
const DEFAULT_SIZE = 25;

/** The instant every fixture write is timestamped against — the same one `@/lib/clock`'s `now()`
 *  pins to in fixture mode. A local literal rather than an import from either module: importing
 *  `now()` from `@/lib/clock` would cycle back here (it reads `USE_FIXTURES` from this file), and
 *  importing from `./fixtures` would defeat the dynamic `import()` above. */
const FIXTURE_CLOCK = new Date("2026-08-24T18:00:00Z");


function nextNumber(prefix: string, ids: string[]): string {
  const max = ids.reduce((best, id) => {
    const n = Number(id.slice(prefix.length));
    return Number.isFinite(n) && n > best ? n : best;
  }, 0);
  return `${prefix}${max + 1}`;
}

/* --------------------------------- errors -------------------------------- */

/** A fixture-mode failure shaped like a real `ApiError`, so `problemMessage` and
 *  `fieldErrorMap` render exactly as they would against the gateway. */
function fixtureProblem(status: number, detail: string, field?: string): ApiError {
  return new ApiError(
    status,
    detail,
    field ? { detail, errors: [{ field, code: "invalid", message: detail }] } : { detail },
  );
}

/* ------------------------------- incidents ------------------------------- */

export type IncidentSort = "updated" | "priority" | "created";

export interface ListIncidentsParams {
  search?: string;
  priority?: Priority | "all";
  status?: IncidentStatus | "all";
  sort?: IncidentSort;
  page?: number;
  size?: number;
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

function withoutWorklog(incident: Incident): Incident {
  return { ...incident, worklog: [] };
}

export async function listIncidents(params: ListIncidentsParams = {}): Promise<Page<Incident>> {
  const page = params.page ?? DEFAULT_PAGE;
  const size = params.size ?? DEFAULT_SIZE;
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = fx.INCIDENTS.filter((i) => {
      const matchesQuery =
        q === "" ||
        i.id.toLowerCase().includes(q) ||
        i.summary.toLowerCase().includes(q) ||
        i.assignee.toLowerCase().includes(q) ||
        i.requester.toLowerCase().includes(q);
      const matchesPriority =
        !params.priority || params.priority === "all" || i.priority === params.priority;
      const matchesStatus =
        !params.status || params.status === "all" || i.status === params.status;
      return matchesQuery && matchesPriority && matchesStatus;
    });
    const sorted = sortIncidents(filtered, params.sort ?? "updated").map(withoutWorklog);
    return settle(paginate(sorted, page, size));
  }

  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.priority && params.priority !== "all") query.set("priority", params.priority);
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.sort) query.set("sort", params.sort);
  return apiRequest<Page<Incident>>(`/itsm/incidents?${query.toString()}`);
}

export async function getIncident(number: string): Promise<Incident> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const found = fx.INCIDENTS.find((i) => i.id === number);
    if (!found) throw fixtureProblem(404, `Incident ${number} not found.`);
    return settle(found);
  }
  return apiRequest<Incident>(`/itsm/incidents/${encodeURIComponent(number)}`);
}

/** `POST /itsm/incidents`. */
export async function createIncident(payload: IncidentIn): Promise<Incident> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    if (payload.affected_asset_tag && !fx.ASSETS.some((a) => a.tag === payload.affected_asset_tag)) {
      throw fixtureProblem(404, "That asset tag isn't in the CMDB.", "affected_asset_tag");
    }
    const id = nextNumber("INC-", fx.INCIDENTS.map((i) => i.id));
    const at = FIXTURE_CLOCK.toISOString();
    const incident: Incident = {
      id,
      summary: payload.summary,
      description: payload.description ?? "",
      priority: payload.priority ?? "P3",
      status: "new",
      category: payload.category ?? "software",
      assignee: payload.assignee ?? "",
      requester: payload.requester,
      affected_asset_tag: payload.affected_asset_tag ?? null,
      sla_state: "on_track",
      sla_minutes_remaining: { P1: 120, P2: 240, P3: 480, P4: 1440 }[payload.priority ?? "P3"],
      created_at: at,
      updated_at: at,
      worklog: [
        {
          id: `wl-${Date.now()}`,
          author: payload.requester,
          body: `Raised as ${payload.priority ?? "P3"}.`,
          at,
          kind: "note",
        },
      ],
    };
    fx.INCIDENTS.unshift(incident);
    return settle(incident);
  }
  return apiRequest<Incident>("/itsm/incidents", { method: "POST", body: payload });
}

/** `POST /itsm/incidents/{number}/worklog`. `kind` is always `"note"`. */
export async function addWorklog(number: string, payload: WorklogIn): Promise<Incident> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const incident = fx.INCIDENTS.find((i) => i.id === number);
    if (!incident) throw fixtureProblem(404, `Incident ${number} not found.`);
    const entry: WorklogEntry = {
      id: `wl-${Date.now()}`,
      author: payload.author,
      body: payload.body,
      at: FIXTURE_CLOCK.toISOString(),
      kind: "note",
    };
    incident.worklog = [...incident.worklog, entry];
    incident.updated_at = entry.at;
    return settle(incident);
  }
  return apiRequest<Incident>(`/itsm/incidents/${encodeURIComponent(number)}/worklog`, {
    method: "POST",
    body: payload,
  });
}

/** `PATCH /itsm/incidents/{number}`. `priority` and `assignee` need ORG scope server-side. */
export async function updateIncident(number: string, payload: IncidentUpdate): Promise<Incident> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const incident = fx.INCIDENTS.find((i) => i.id === number);
    if (!incident) throw fixtureProblem(404, `Incident ${number} not found.`);
    if (
      payload.affected_asset_tag &&
      !fx.ASSETS.some((a) => a.tag === payload.affected_asset_tag)
    ) {
      throw fixtureProblem(404, "That asset tag isn't in the CMDB.", "affected_asset_tag");
    }
    const at = FIXTURE_CLOCK.toISOString();
    const notes: WorklogEntry[] = [];
    if (payload.priority && payload.priority !== incident.priority) {
      notes.push({
        id: `wl-${Date.now()}-p`,
        author: "System",
        body: `Priority ${incident.priority} → ${payload.priority}.`,
        at,
        kind: "status",
      });
    }
    if (payload.assignee !== undefined && payload.assignee !== incident.assignee) {
      notes.push({
        id: `wl-${Date.now()}-a`,
        author: "System",
        body: payload.assignee ? `Assigned to ${payload.assignee}.` : "Unassigned.",
        at,
        kind: "assignment",
      });
    }
    Object.assign(incident, {
      ...(payload.summary !== undefined ? { summary: payload.summary } : {}),
      ...(payload.description !== undefined ? { description: payload.description } : {}),
      ...(payload.category !== undefined ? { category: payload.category } : {}),
      ...(payload.priority !== undefined ? { priority: payload.priority } : {}),
      ...(payload.assignee !== undefined ? { assignee: payload.assignee } : {}),
      ...(payload.affected_asset_tag !== undefined
        ? { affected_asset_tag: payload.affected_asset_tag }
        : {}),
      updated_at: at,
      worklog: [...incident.worklog, ...notes],
    });
    return settle(incident);
  }
  return apiRequest<Incident>(`/itsm/incidents/${encodeURIComponent(number)}`, {
    method: "PATCH",
    body: payload,
  });
}

/** `POST /itsm/incidents/{number}/status`. `note` is required when resolving. */
export async function changeIncidentStatus(
  number: string,
  payload: IncidentStatusIn,
): Promise<Incident> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const incident = fx.INCIDENTS.find((i) => i.id === number);
    if (!incident) throw fixtureProblem(404, `Incident ${number} not found.`);
    const at = FIXTURE_CLOCK.toISOString();
    const entry: WorklogEntry = {
      id: `wl-${Date.now()}-s`,
      author: "System",
      body: payload.note ?? `Status changed to ${payload.status}.`,
      at,
      kind: payload.status === "resolved" ? "resolution" : "status",
    };
    incident.status = payload.status;
    if (payload.status === "resolved" || payload.status === "closed") {
      incident.sla_state = incident.sla_state === "breached" ? "breached" : "met";
      incident.sla_minutes_remaining = null;
    }
    incident.updated_at = at;
    incident.worklog = [...incident.worklog, entry];
    return settle(incident);
  }
  return apiRequest<Incident>(`/itsm/incidents/${encodeURIComponent(number)}/status`, {
    method: "POST",
    body: payload,
  });
}

/** `DELETE /itsm/incidents/{number}` — soft delete, ORG scope. */
export async function deleteIncident(number: string): Promise<void> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const index = fx.INCIDENTS.findIndex((i) => i.id === number);
    if (index === -1) throw fixtureProblem(404, `Incident ${number} not found.`);
    fx.INCIDENTS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/itsm/incidents/${encodeURIComponent(number)}`, { method: "DELETE" });
}

/* --------------------------- service requests --------------------------- */

export interface ListRequestsParams {
  search?: string;
  status?: RequestStatus | "all";
  page?: number;
  size?: number;
}

export async function listRequests(params: ListRequestsParams = {}): Promise<Page<ServiceRequest>> {
  const page = params.page ?? DEFAULT_PAGE;
  const size = params.size ?? DEFAULT_SIZE;
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = fx.REQUESTS.filter((r) => {
      const matchesQuery =
        q === "" ||
        r.id.toLowerCase().includes(q) ||
        r.requester.toLowerCase().includes(q) ||
        r.subject.toLowerCase().includes(q);
      const matchesStatus =
        !params.status || params.status === "all" || r.status === params.status;
      return matchesQuery && matchesStatus;
    });
    const sorted = [...filtered].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    return settle(paginate(sorted, page, size));
  }

  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  return apiRequest<Page<ServiceRequest>>(`/itsm/service-requests?${query.toString()}`);
}

export async function getRequest(number: string): Promise<ServiceRequest> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const found = fx.REQUESTS.find((r) => r.id === number);
    if (!found) throw fixtureProblem(404, `Request ${number} not found.`);
    return settle(found);
  }
  return apiRequest<ServiceRequest>(`/itsm/service-requests/${encodeURIComponent(number)}`);
}

/** `POST /itsm/service-requests`. */
export async function createRequest(payload: ServiceRequestIn): Promise<ServiceRequest> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const item = fx.CATALOG.find((c) => c.id === payload.catalog_item_id);
    if (!item) throw fixtureProblem(404, "That catalog item wasn't found.", "catalog_item_id");
    const id = nextNumber("REQ-", fx.REQUESTS.map((r) => r.id));
    const at = FIXTURE_CLOCK.toISOString();
    const request: ServiceRequest = {
      id,
      catalog_item_id: item.id,
      catalog_item_name: item.name,
      category: item.category,
      requester: payload.requester,
      subject: payload.subject,
      status: "approval",
      assignee: null,
      decision_note: null,
      created_at: at,
      updated_at: at,
    };
    fx.REQUESTS.unshift(request);
    return settle(request);
  }
  return apiRequest<ServiceRequest>("/itsm/service-requests", { method: "POST", body: payload });
}

/** `POST /itsm/service-requests/{number}/decision`. `note` is required to reject. */
export async function decideRequest(
  number: string,
  payload: RequestDecisionIn,
): Promise<ServiceRequest> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const request = fx.REQUESTS.find((r) => r.id === number);
    if (!request) throw fixtureProblem(404, `Request ${number} not found.`);
    if (request.status !== "approval") {
      throw fixtureProblem(409, "This request is no longer awaiting approval.");
    }
    request.status = payload.decision === "approve" ? "in_progress" : "rejected";
    request.decision_note = payload.note ?? null;
    request.updated_at = FIXTURE_CLOCK.toISOString();
    return settle(request);
  }
  return apiRequest<ServiceRequest>(`/itsm/service-requests/${encodeURIComponent(number)}/decision`, {
    method: "POST",
    body: payload,
  });
}

/** `POST /itsm/service-requests/{number}/fulfil`. */
export async function fulfilRequest(number: string): Promise<ServiceRequest> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const request = fx.REQUESTS.find((r) => r.id === number);
    if (!request) throw fixtureProblem(404, `Request ${number} not found.`);
    if (request.status !== "in_progress") {
      throw fixtureProblem(409, "This request is not in progress.");
    }
    request.status = "fulfilled";
    request.assignee = null;
    request.updated_at = FIXTURE_CLOCK.toISOString();
    return settle(request);
  }
  return apiRequest<ServiceRequest>(`/itsm/service-requests/${encodeURIComponent(number)}/fulfil`, {
    method: "POST",
  });
}

/** `PATCH /itsm/service-requests/{number}` — assign or unassign. */
export async function updateRequest(number: string, payload: RequestPatch): Promise<ServiceRequest> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const request = fx.REQUESTS.find((r) => r.id === number);
    if (!request) throw fixtureProblem(404, `Request ${number} not found.`);
    request.assignee = payload.assignee;
    request.updated_at = FIXTURE_CLOCK.toISOString();
    return settle(request);
  }
  return apiRequest<ServiceRequest>(`/itsm/service-requests/${encodeURIComponent(number)}`, {
    method: "PATCH",
    body: payload,
  });
}

/** `DELETE /itsm/service-requests/{number}` — cancel, soft delete, ORG scope. */
export async function deleteRequest(number: string): Promise<void> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const index = fx.REQUESTS.findIndex((r) => r.id === number);
    if (index === -1) throw fixtureProblem(404, `Request ${number} not found.`);
    fx.REQUESTS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/itsm/service-requests/${encodeURIComponent(number)}`, { method: "DELETE" });
}

/* ------------------------------ catalog ---------------------------------- */

export interface ListCatalogParams {
  page?: number;
  size?: number;
}

export async function listCatalog(params: ListCatalogParams = {}): Promise<Page<CatalogItem>> {
  const page = params.page ?? 1;
  const size = params.size ?? 100;
  if (USE_FIXTURES) {
    const fx = await fixtures();
    return settle(paginate(fx.CATALOG, page, size));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  return apiRequest<Page<CatalogItem>>(`/itsm/catalog?${query.toString()}`);
}

/** `POST /itsm/catalog`. */
export async function createCatalogItem(payload: CatalogItemIn): Promise<CatalogItem> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    if (fx.CATALOG.some((c) => c.id === payload.id)) {
      throw fixtureProblem(409, "That code is already in use.", "id");
    }
    const item: CatalogItem = {
      id: payload.id,
      name: payload.name,
      category: payload.category,
      description: payload.description ?? "",
      sla_days: payload.sla_days ?? 1,
      requests_30d: 0,
    };
    fx.CATALOG.push(item);
    return settle(item);
  }
  return apiRequest<CatalogItem>("/itsm/catalog", { method: "POST", body: payload });
}

/** `PATCH /itsm/catalog/{code}`. */
export async function updateCatalogItem(
  code: string,
  payload: CatalogItemUpdate,
): Promise<CatalogItem> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const item = fx.CATALOG.find((c) => c.id === code);
    if (!item) throw fixtureProblem(404, `Catalog item ${code} not found.`);
    Object.assign(item, payload);
    return settle(item);
  }
  return apiRequest<CatalogItem>(`/itsm/catalog/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: payload,
  });
}

/** `DELETE /itsm/catalog/{code}` — retire, soft delete, ORG scope. */
export async function deleteCatalogItem(code: string): Promise<void> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const index = fx.CATALOG.findIndex((c) => c.id === code);
    if (index === -1) throw fixtureProblem(404, `Catalog item ${code} not found.`);
    fx.CATALOG.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/itsm/catalog/${encodeURIComponent(code)}`, { method: "DELETE" });
}

/* -------------------------------- assets -------------------------------- */

export interface ListAssetsParams {
  search?: string;
  type?: AssetType | "all";
  status?: AssetStatus | "all";
  page?: number;
  size?: number;
  /** Aborts the request: the import/export dialog cancels a load or an export with it. */
  signal?: AbortSignal | undefined;
}

export async function listAssets(params: ListAssetsParams = {}): Promise<Page<Asset>> {
  const page = params.page ?? DEFAULT_PAGE;
  const size = params.size ?? DEFAULT_SIZE;
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = fx.ASSETS.filter((a) => {
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
    const sorted = [...filtered].sort((a, b) => a.tag.localeCompare(b.tag));
    return settle(paginate(sorted, page, size));
  }

  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.type && params.type !== "all") query.set("type", params.type);
  if (params.status && params.status !== "all") query.set("status", params.status);
  return apiRequest<Page<Asset>>(`/itsm/assets?${query.toString()}`, { signal: params.signal });
}

export async function getAsset(tag: string): Promise<Asset> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const found = fx.ASSETS.find((a) => a.tag === tag);
    if (!found) throw fixtureProblem(404, `Asset ${tag} not found.`);
    return settle(found);
  }
  return apiRequest<Asset>(`/itsm/assets/${encodeURIComponent(tag)}`);
}

/** `POST /itsm/assets`. */
export async function createAsset(payload: AssetIn): Promise<Asset> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    if (fx.ASSETS.some((a) => a.tag === payload.tag)) {
      throw fixtureProblem(409, "That tag is already in use.", "tag");
    }
    const asset: Asset = {
      tag: payload.tag,
      name: payload.name,
      type: payload.type,
      status: payload.status ?? "in_stock",
      owner: payload.owner ?? null,
      location: payload.location ?? "",
      serial: payload.serial ?? "",
      purchased_at: payload.purchased_at ?? null,
      warranty_expires_at: payload.warranty_expires_at ?? null,
      cost_minor: payload.cost_minor ?? 0,
      cost_currency: payload.cost_currency ?? "INR",
      open_incident_ids: [],
    };
    fx.ASSETS.push(asset);
    return settle(asset);
  }
  return apiRequest<Asset>("/itsm/assets", { method: "POST", body: payload });
}

/** `PATCH /itsm/assets/{tag}` — `tag` is immutable and never appears in `payload`. */
export async function updateAsset(tag: string, payload: AssetUpdate): Promise<Asset> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const asset = fx.ASSETS.find((a) => a.tag === tag);
    if (!asset) throw fixtureProblem(404, `Asset ${tag} not found.`);
    if (Object.keys(payload).length === 0) {
      throw fixtureProblem(422, "Provide at least one field to update");
    }
    Object.assign(asset, payload);
    asset.cost_currency = asset.cost_currency.toUpperCase();
    return settle(asset);
  }
  return apiRequest<Asset>(`/itsm/assets/${encodeURIComponent(tag)}`, {
    method: "PATCH",
    body: payload,
  });
}

/** `DELETE /itsm/assets/{tag}` — soft delete, ORG scope; 409 while open incidents name it. */
export async function deleteAsset(tag: string): Promise<void> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const asset = fx.ASSETS.find((a) => a.tag === tag);
    if (!asset) throw fixtureProblem(404, `Asset ${tag} not found.`);
    if (asset.open_incident_ids.length > 0) {
      throw fixtureProblem(
        409,
        `${asset.open_incident_ids.join(", ")} still name this asset as affected.`,
      );
    }
    fx.ASSETS.splice(fx.ASSETS.indexOf(asset), 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/itsm/assets/${encodeURIComponent(tag)}`, { method: "DELETE" });
}

/* -------------------------------- overview -------------------------------- */

function fixtureBreakdown<K extends string>(
  items: Incident[],
  key: (i: Incident) => K,
  order: readonly K[],
  labels: Record<K, string>,
): { key: string; label: string; count: number; pct: number }[] {
  const open = items.filter((i) => i.status !== "resolved" && i.status !== "closed");
  const total = open.length;
  return order
    .map((k) => {
      const count = open.filter((i) => key(i) === k).length;
      return { key: k, label: labels[k], count, pct: total === 0 ? 0 : Math.round((count / total) * 100) };
    })
    .filter((b) => b.count > 0);
}

const PRIORITY_LABELS: Record<Priority, string> = {
  P1: "P1 · Critical",
  P2: "P2 · High",
  P3: "P3 · Moderate",
  P4: "P4 · Low",
};
const CATEGORY_LABELS: Record<IncidentCategory, string> = {
  network: "Network",
  hardware: "Hardware",
  software: "Software",
  access: "Access",
  email: "Email",
  security: "Security",
};

/** `GET /itsm/overview` — the service-desk rollups, computed over every in-scope row. */
export async function getOverview(): Promise<Overview> {
  if (USE_FIXTURES) {
    const fx = await fixtures();
    const open = fx.INCIDENTS.filter((i) => i.status !== "resolved" && i.status !== "closed");
    const breached = fx.INCIDENTS.filter((i) => i.sla_state === "breached").length;
    const byPriority = fixtureBreakdown(
      fx.INCIDENTS,
      (i) => i.priority,
      ["P1", "P2", "P3", "P4"] as const,
      PRIORITY_LABELS,
    ).sort((a, b) => a.key.localeCompare(b.key));
    const byCategory = fixtureBreakdown(
      fx.INCIDENTS,
      (i) => i.category,
      ["network", "hardware", "software", "access", "email", "security"] as const,
      CATEGORY_LABELS,
    ).sort((a, b) => b.count - a.count);
    return settle({
      open_incidents: open.length,
      sla_compliance_pct:
        fx.INCIDENTS.length === 0
          ? 100
          : Math.round(((fx.INCIDENTS.length - breached) / fx.INCIDENTS.length) * 1000) / 10,
      requests_awaiting_approval: fx.REQUESTS.filter((r) => r.status === "approval").length,
      assets_under_management: fx.ASSETS.filter((a) => a.status !== "retired").length,
      volume: [
        { label: "27 Jul", opened: 4, resolved: 3 },
        { label: "03 Aug", opened: 6, resolved: 5 },
        { label: "10 Aug", opened: 5, resolved: 6 },
        { label: "17 Aug", opened: 8, resolved: 4 },
        { label: "24 Aug", opened: open.length, resolved: fx.INCIDENTS.length - open.length },
      ],
      by_priority: byPriority,
      by_category: byCategory,
    });
  }
  return apiRequest<Overview>("/itsm/overview");
}
