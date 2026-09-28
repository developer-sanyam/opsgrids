/**
 * Typed Operations API client — the worker's own surface of it.
 *
 * Live only: there is no fixture mode here (contrast `product-operations`' `VITE_USE_FIXTURES`)
 * — a picker's shift is real data or nothing, and a fixture path with nothing honest to render
 * was this app's own rule before this slice (`AuthProvider`'s doc comment) and stays it now.
 *
 * Every function calls the operations service through the gateway at the same-origin
 * `/api/v1/operations/...` prefix (Vite proxies it to the gateway in dev, `vite.config.ts`).
 */

import { apiRequest } from "@opsgrids/client";

import type {
  AssignmentOfferOut,
  ExceptionResolveIn,
  LocationOut,
  Page,
  PackageIn,
  PackageOut,
  PackagingMaterialOut,
  PickConfirmIn,
  PickTaskOut,
  PickTaskStatus,
  ScanIn,
  TaskReleaseIn,
  WorkerOut,
  WorkerPresenceIn,
} from "./types";

const DEFAULT_PAGE_SIZE = 25;
/** The whole (small) working set — used where a screen genuinely needs it: resolving "which
 *  worker am I" from the roster, and gathering every task/package an order has across zones. */
export const FULL_PAGE = 200;

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    query.set(key, String(value));
  }
  const s = query.toString();
  return s ? `?${s}` : "";
}

/* ---------------------------------- workers ---------------------------------- */

export interface ListWorkersParams {
  warehouse_id?: string | undefined;
  is_active?: boolean | undefined;
  q?: string | undefined;
  page?: number | undefined;
  size?: number | undefined;
}

/** `GET /operations/fulfilment/workers` — `operations.fulfilment.worker.read:ORG`. */
export async function listWorkers(params: ListWorkersParams = {}): Promise<Page<WorkerOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    warehouse_id: params.warehouse_id,
    is_active: params.is_active,
    q: params.q,
  });
  return apiRequest<Page<WorkerOut>>(`/operations/fulfilment/workers${query}`);
}

/** `POST /operations/fulfilment/workers/{id}/presence` — `operations.fulfilment.worker.write:OWN`
 *  covers setting one's own. */
export async function setWorkerPresence(id: string, input: WorkerPresenceIn): Promise<WorkerOut> {
  return apiRequest<WorkerOut>(`/operations/fulfilment/workers/${encodeURIComponent(id)}/presence`, {
    method: "POST",
    body: input,
  });
}

/* ---------------------------------- pick tasks ---------------------------------- */

export interface ListTasksParams {
  status?: PickTaskStatus | undefined;
  warehouse_id?: string | undefined;
  order_id?: string | undefined;
  page?: number | undefined;
  size?: number | undefined;
}

/** `GET /operations/fulfilment/tasks` — `operations.fulfilment.task.read:ORG`. There is no
 *  `mine`/`worker_id` filter on this endpoint, so "my tasks" is resolved client-side from each
 *  task's own `offers` against the caller's own worker id (`useMyWorker`). */
export async function listTasks(params: ListTasksParams = {}): Promise<Page<PickTaskOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    status: params.status,
    warehouse_id: params.warehouse_id,
    order_id: params.order_id,
  });
  return apiRequest<Page<PickTaskOut>>(`/operations/fulfilment/tasks${query}`);
}

/** `GET /operations/fulfilment/tasks/{id}` — `operations.fulfilment.task.read:ORG`. */
export async function getTask(id: string): Promise<PickTaskOut> {
  return apiRequest<PickTaskOut>(`/operations/fulfilment/tasks/${encodeURIComponent(id)}`);
}

/** `POST /operations/fulfilment/offers/{id}/accept` — `operations.fulfilment.task.execute:OWN`.
 *  The exact ADR-0025 §3 conditional update; a zero-row update on the server is a 409, not a
 *  500 — "that offer is no longer yours to accept" (expired, withdrawn, already answered). */
export async function acceptOffer(offerId: string): Promise<AssignmentOfferOut> {
  return apiRequest<AssignmentOfferOut>(
    `/operations/fulfilment/offers/${encodeURIComponent(offerId)}/accept`,
    { method: "POST" },
  );
}

/** `POST /operations/fulfilment/offers/{id}/decline` — `operations.fulfilment.task.execute:OWN`. */
export async function declineOffer(offerId: string): Promise<AssignmentOfferOut> {
  return apiRequest<AssignmentOfferOut>(
    `/operations/fulfilment/offers/${encodeURIComponent(offerId)}/decline`,
    { method: "POST" },
  );
}

/** `POST /operations/fulfilment/tasks/{id}/release` — `operations.fulfilment.task.execute:OWN`.
 *  Hands an `ACCEPTED` task back before the first scan/pick — after that it is `IN_PROGRESS` and
 *  the only way out is an exception or finishing it. */
export async function releaseTask(taskId: string, input: TaskReleaseIn = {}): Promise<PickTaskOut> {
  return apiRequest<PickTaskOut>(`/operations/fulfilment/tasks/${encodeURIComponent(taskId)}/release`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/fulfilment/tasks/{id}/scan` — `operations.fulfilment.task.execute:OWN`. The
 *  append-only scan log; the first one on a task also starts it. Safe to replay (see the offline
 *  queue, `lib/scanQueue.ts`) — a duplicate scan row is a log fact repeated, never a stock move. */
export async function scanTask(taskId: string, input: ScanIn): Promise<PickTaskOut> {
  return apiRequest<PickTaskOut>(`/operations/fulfilment/tasks/${encodeURIComponent(taskId)}/scan`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/fulfilment/tasks/{id}/lines/{line_id}/pick` — `task.execute:OWN`. No client
 *  idempotency key is accepted here (see `api/types.ts`'s header) — a genuine network-retry with
 *  an explicit partial quantity is not guaranteed to be a no-op, so the caller must reconcile
 *  against the fresh `PickTaskOut` this returns rather than assume the first attempt was lost. */
export async function confirmPick(
  taskId: string,
  lineId: string,
  input: PickConfirmIn,
): Promise<PickTaskOut> {
  return apiRequest<PickTaskOut>(
    `/operations/fulfilment/tasks/${encodeURIComponent(taskId)}/lines/${encodeURIComponent(lineId)}/pick`,
    { method: "POST", body: input },
  );
}

/** `POST /operations/fulfilment/tasks/{id}/exceptions/{id}/resolve` — `task.execute:OWN`. */
export async function resolveException(
  taskId: string,
  exceptionId: string,
  input: ExceptionResolveIn = {},
): Promise<PickTaskOut> {
  return apiRequest<PickTaskOut>(
    `/operations/fulfilment/tasks/${encodeURIComponent(taskId)}/exceptions/${encodeURIComponent(exceptionId)}/resolve`,
    { method: "POST", body: input },
  );
}

/* ---------------------------------- packing ---------------------------------- */

/** `GET /operations/fulfilment/packaging-materials` — `operations.fulfilment.pack.read:ORG`. */
export async function listPackagingMaterials(
  params: { page?: number | undefined; size?: number | undefined } = {},
): Promise<Page<PackagingMaterialOut>> {
  const query = buildQuery({ page: params.page ?? 1, size: params.size ?? FULL_PAGE });
  return apiRequest<Page<PackagingMaterialOut>>(`/operations/fulfilment/packaging-materials${query}`);
}

/** `GET /operations/fulfilment/packages` — `operations.fulfilment.pack.read:ORG`. */
export async function listPackages(
  params: { order_id?: string | undefined; page?: number | undefined; size?: number | undefined } = {},
): Promise<Page<PackageOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? FULL_PAGE,
    order_id: params.order_id,
  });
  return apiRequest<Page<PackageOut>>(`/operations/fulfilment/packages${query}`);
}

/** `POST /operations/fulfilment/packages` — `operations.fulfilment.pack.execute:ORG`. Opens,
 *  fills and posts the relocation in one call — a 409 names the SKU if a line asks for more than
 *  is staged-and-unpacked, or the order left `IN_FULFILMENT`/`READY` already. No client
 *  idempotency key (see `api/types.ts`'s header): reconcile against the response, never assume. */
export async function confirmPackage(input: PackageIn): Promise<PackageOut> {
  return apiRequest<PackageOut>("/operations/fulfilment/packages", { method: "POST", body: input });
}

/* ---------------------------------- locations ---------------------------------- */

/**
 * `GET /operations/locations/by-barcode/{barcode}` — `operations.inventory.location.read`, a
 * permission the `OPERATIONS_WORKER` role template does not grant (`app/catalogue.py`). Every
 * call from this app gets a 403 until that catalogue gap is closed — see this app's `README.md`.
 * Callers must treat a 403 here as "can't verify", not "wrong", and degrade accordingly.
 */
export async function resolveLocationByBarcode(barcode: string): Promise<LocationOut> {
  return apiRequest<LocationOut>(`/operations/locations/by-barcode/${encodeURIComponent(barcode)}`);
}
