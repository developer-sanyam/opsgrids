/**
 * Catalog's operator surface — `/api/v1/admin/catalog`, fronted by admin-gateway (`staff_only`).
 *
 * Wire types mirror `services/catalog/app/schemas.py` (`libs/contracts/openapi/catalog.json`)
 * field for field. Reads are `owner` and `billing_ops`; every write is `owner` only, carries a
 * `reason`, and **requires** an `Idempotency-Key` — the caller makes one per dialog open and sends
 * it on every retry from that dialog, so a double click or a dropped response never changes the
 * catalogue twice (the server replays the first answer with `Idempotent-Replayed: true`).
 *
 * Kept apart from `client.ts` so the catalogue page owns its contract in one file. Fixtures mode
 * loads its in-memory catalogue through a dynamic `import()` only, for the same reason `client.ts`
 * does: a production build must provably never ship the sample data.
 */

import { ApiError, apiRequest } from "./http";
import { USE_FIXTURES } from "./client";

export type * from "./catalogTypes";
import type {
  Audience,
  DeletedVersionOut,
  ListPlansParams,
  PlanCreateRequest,
  PlanDetailOut,
  PlanListItemOut,
  PlanListOut,
  PlanVersionOut,
  ProblemFieldError,
  VersionCreateRequest,
} from "./catalogTypes";

/** The `errors[]` a 422 carries (publish validation, registry checks), read defensively. */
export function problemErrors(error: unknown): ProblemFieldError[] {
  if (!(error instanceof ApiError) || error.problem === null) return [];
  const raw: unknown = (error.problem as { errors?: unknown }).errors;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item: unknown) => {
    if (typeof item !== "object" || item === null) return [];
    const { field, code, message } = item as Record<string, unknown>;
    return typeof message === "string"
      ? [{ field: String(field ?? ""), code: String(code ?? ""), message }]
      : [];
  });
}

/** `problem.request_id`, for an error's reference line. */
export function problemReference(error: unknown): string | undefined {
  return error instanceof ApiError ? (error.problem?.request_id ?? undefined) : undefined;
}

function loadFixtures() {
  return import("./fixtures/catalogAdmin");
}

const BASE = "/admin/catalog";

function write<T>(
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  body: unknown,
  idempotencyKey: string,
): Promise<T> {
  return apiRequest<T>(path, { method, body, headers: { "Idempotency-Key": idempotencyKey } });
}

/* ------------------------------- reads ------------------------------- */

export async function listPlans(params: ListPlansParams = {}): Promise<PlanListOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureListPlans(params);
  const query = new URLSearchParams();
  if (params.productKey) query.set("product_key", params.productKey);
  query.set("audience", params.audience ?? "public");
  if (params.state) query.set("state", params.state);
  if (params.cursor) query.set("cursor", params.cursor);
  query.set("limit", String(params.limit ?? 25));
  return apiRequest<PlanListOut>(`${BASE}/plans?${query.toString()}`);
}

export async function getPlan(
  planId: string,
  params: { audience?: Audience; cursor?: string | undefined; limit?: number } = {},
): Promise<PlanDetailOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureGetPlan(planId, params);
  const query = new URLSearchParams();
  query.set("audience", params.audience ?? "public");
  if (params.cursor) query.set("cursor", params.cursor);
  query.set("limit", String(params.limit ?? 25));
  return apiRequest<PlanDetailOut>(`${BASE}/plans/${encodeURIComponent(planId)}?${query.toString()}`);
}

export async function getVersion(versionId: string): Promise<PlanVersionOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureGetVersion(versionId);
  return apiRequest<PlanVersionOut>(`${BASE}/versions/${encodeURIComponent(versionId)}`);
}

/* --------------------------- writes (owner) -------------------------- */

export async function createPlan(request: PlanCreateRequest, key: string): Promise<PlanListItemOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureCreatePlan(request);
  return write<PlanListItemOut>(`${BASE}/plans`, "POST", request, key);
}

export async function retirePlan(planId: string, reason: string, key: string): Promise<PlanListItemOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureSetPlanState(planId, "retired");
  return write(`${BASE}/plans/${encodeURIComponent(planId)}/retire`, "POST", { reason }, key);
}

export async function reactivatePlan(
  planId: string,
  reason: string,
  key: string,
): Promise<PlanListItemOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureSetPlanState(planId, "active");
  return write(`${BASE}/plans/${encodeURIComponent(planId)}/reactivate`, "POST", { reason }, key);
}

export async function createVersion(
  planId: string,
  request: VersionCreateRequest,
  key: string,
): Promise<PlanVersionOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureCreateVersion(planId, request);
  return write(`${BASE}/plans/${encodeURIComponent(planId)}/versions`, "POST", request, key);
}

export async function publishVersion(
  versionId: string,
  request: { reason: string; allow_zero_amounts: boolean },
  key: string,
): Promise<PlanVersionOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureSetVersionState(versionId, "published");
  return write(`${BASE}/versions/${encodeURIComponent(versionId)}/publish`, "POST", request, key);
}

export async function withdrawVersion(
  versionId: string,
  reason: string,
  key: string,
): Promise<PlanVersionOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureSetVersionState(versionId, "grandfathered");
  return write(`${BASE}/versions/${encodeURIComponent(versionId)}/withdraw`, "POST", { reason }, key);
}

export async function deleteDraftVersion(
  versionId: string,
  reason: string,
  key: string,
): Promise<DeletedVersionOut> {
  if (USE_FIXTURES) return (await loadFixtures()).fixtureDeleteVersion(versionId);
  return write(`${BASE}/versions/${encodeURIComponent(versionId)}`, "DELETE", { reason }, key);
}
