/**
 * The delivery module's own API client, against `libs/contracts/openapi/storefront.json`.
 *
 * It sits beside `src/api/client.ts` rather than inside it: that file is the store document and
 * the catalogue, owned elsewhere, and this is a different module of the same service with its own
 * permissions and its own fixtures. Both share the transport (`apiRequest`) and the session, which
 * is the part that must not be forked.
 *
 * **Fixtures by default**, as everywhere else in this app. `VITE_USE_FIXTURES=false` points every
 * call below at the gateway, which now has the two storefront route entries ADR-0010 §4 specifies.
 */

import {
  API_BASE,
  ApiError,
  apiRequest,
  getAccessToken,
  notifyUnauthorized,
  settle,
  type ProblemJson,
} from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import { FIXTURE_DEMAND, FIXTURE_POSTAL_CODES, FIXTURE_STORE, FIXTURE_ZONES } from "./fixtures";
import { parseImportCsv, parsePostalCodeText } from "./postalCodes";
import type { PostalCodeMap, PostalCodeOwner } from "./postalCodes";
import type {
  DemandRowOut,
  DemandTotal,
  ImportMode,
  ImportResultOut,
  MerchantServiceabilityOut,
  Page,
  StoreOut,
  ZoneCreate,
  ZoneOut,
  ZonePostalCodesOut,
} from "./types";

/** Re-exported so screens can wear the sample marker without importing two modules. */
export { USE_FIXTURES };

/**
 * How long the tester waits before calling it unknown.
 *
 * ADR-0010 §13 gives the serviceability path a 2-second server budget, after which it 503s. This
 * sits beyond it so a slow-but-working check is never cut off by the client first, and short
 * enough that a merchant is not left watching a spinner wonder whether they broke something.
 *
 * The request is not aborted, only abandoned: `RequestOptions` in `@opsgrids/client` carries no
 * `signal`, and that module is owned elsewhere. The cost is one in-flight request outliving its
 * screen, which is harmless — the server has already done the work and the demand counter is
 * written in the same transaction either way (§7).
 */
const CHECK_TIMEOUT_MS = 6_000;

/** One page per request. The zone list is a dozen rows; the demand table is the one that pages. */
const PAGE_SIZE = 200;

/**
 * How many demand pages to walk before stopping.
 *
 * The endpoint returns a row per postal code **per day** (`DemandRowOut`), so a wide range over a
 * busy store is thousands of rows. Ten pages is 2,000 rows — enough for any realistic range at the
 * ADR's own planning scale (§12: ~50–300 counted rows a day) — and when it is not, the screen says
 * the report is truncated rather than quietly reporting the wrong leader.
 */
const DEMAND_PAGE_CAP = 10;

class TimeoutError extends Error {
  constructor() {
    super("timed out");
    this.name = "TimeoutError";
  }
}

function withTimeout<T>(work: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    work,
    new Promise<T>((_resolve, reject) => {
      setTimeout(() => reject(new TimeoutError()), ms);
    }),
  ]);
}

/* ------------------------------- fixture state -------------------------------- */

/** Mutable only in fixture mode; every live call goes to the server and holds nothing. */
let fixtureZones: ZoneOut[] = FIXTURE_ZONES.map((zone) => ({ ...zone }));
let fixtureCodes: Record<string, string[]> = Object.fromEntries(
  Object.entries(FIXTURE_POSTAL_CODES).map(([id, codes]) => [id, [...codes]]),
);

function fixtureOwnerOf(postalCode: string): string | null {
  for (const [zoneId, codes] of Object.entries(fixtureCodes)) {
    if (codes.includes(postalCode)) return zoneId;
  }
  return null;
}

function withCounts(zone: ZoneOut): ZoneOut {
  return { ...zone, postal_code_count: (fixtureCodes[zone.id] ?? []).length };
}

/* ---------------------------------- store ------------------------------------ */

/**
 * `GET /storefront/store` → `StoreOut` (ADR-0010 §1).
 *
 * Read here for three facts this module cannot invent — the ISO currency every minor amount is
 * denominated in, the timezone a `DATE_OFFSET` cutoff is a wall clock in, and whether the store is
 * published at all (an unpublished store answers 404 to every buyer, §4).
 */
export async function getStoreSettings(): Promise<StoreOut> {
  if (USE_FIXTURES) return settle(FIXTURE_STORE, 120);
  return apiRequest<StoreOut>("/storefront/store");
}

/* ---------------------------------- zones ------------------------------------ */

/** `GET /delivery/zones` → `Page_ZoneOut_`. A merchant's zone count is small; one page is all of it. */
export async function listZones(): Promise<ZoneOut[]> {
  if (USE_FIXTURES) return settle(fixtureZones.map(withCounts), 200);
  const page = await apiRequest<Page<ZoneOut>>(`/storefront/delivery/zones?size=${PAGE_SIZE}`);
  return page.items;
}

export async function createZone(input: ZoneCreate): Promise<ZoneOut> {
  if (USE_FIXTURES) {
    const now = new Date().toISOString();
    const zone: ZoneOut = {
      id: `zone-${Date.now().toString(36)}`,
      code: input.code,
      name: input.name,
      mode: input.mode,
      promise_kind: input.promise_kind ?? null,
      promise_minutes: input.promise_minutes ?? null,
      promise_offset_days: input.promise_offset_days ?? null,
      promise_cutoff_time: input.promise_cutoff_time ?? null,
      delivery_fee_minor: input.delivery_fee_minor ?? 0,
      free_delivery_above_minor: input.free_delivery_above_minor ?? null,
      min_order_minor: input.min_order_minor ?? 0,
      is_active: input.is_active ?? true,
      buyer_note: input.buyer_note ?? null,
      postal_code_count: 0,
      created_at: now,
      updated_at: now,
    };
    fixtureZones = [...fixtureZones, zone];
    fixtureCodes = { ...fixtureCodes, [zone.id]: [] };
    return settle(zone, 320);
  }
  return apiRequest<ZoneOut>("/storefront/delivery/zones", { method: "POST", body: input });
}

export async function updateZone(id: string, input: ZoneCreate): Promise<ZoneOut> {
  if (USE_FIXTURES) {
    const existing = fixtureZones.find((zone) => zone.id === id);
    if (!existing) throw new ApiError(404, "That zone no longer exists.", null);
    const updated: ZoneOut = {
      ...existing,
      ...input,
      promise_kind: input.promise_kind ?? null,
      promise_minutes: input.promise_minutes ?? null,
      promise_offset_days: input.promise_offset_days ?? null,
      promise_cutoff_time: input.promise_cutoff_time ?? null,
      delivery_fee_minor: input.delivery_fee_minor ?? 0,
      free_delivery_above_minor: input.free_delivery_above_minor ?? null,
      min_order_minor: input.min_order_minor ?? 0,
      is_active: input.is_active ?? true,
      buyer_note: input.buyer_note ?? null,
      updated_at: new Date().toISOString(),
    };
    fixtureZones = fixtureZones.map((zone) => (zone.id === id ? updated : zone));
    return settle(updated, 320);
  }
  return apiRequest<ZoneOut>(`/storefront/delivery/zones/${id}`, { method: "PATCH", body: input });
}

/**
 * `DELETE /delivery/zones/{id}`.
 *
 * ADR-0010 §9: **the server refuses while postal codes are still mapped to it.** The client does
 * not pre-empt that refusal — it shows the count, sends the request and renders whatever the
 * server says, because the server is the one that knows whether the count is still true.
 */
export async function deleteZone(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const held = (fixtureCodes[id] ?? []).length;
    if (held > 0) {
      throw new ApiError(
        409,
        `That zone still has ${held} postal ${held === 1 ? "code" : "codes"} mapped to it. Move them to another zone first.`,
        null,
      );
    }
    fixtureZones = fixtureZones.filter((zone) => zone.id !== id);
    const rest = { ...fixtureCodes };
    delete rest[id];
    fixtureCodes = rest;
    return settle(undefined, 240);
  }
  return apiRequest<void>(`/storefront/delivery/zones/${id}`, { method: "DELETE" });
}

/* ------------------------------ postal codes --------------------------------- */

/** `GET /delivery/zones/{id}/postal-codes` → `ZonePostalCodesOut`. */
export async function listZonePostalCodes(zoneId: string): Promise<ZonePostalCodesOut> {
  if (USE_FIXTURES) {
    const zone = fixtureZones.find((candidate) => candidate.id === zoneId);
    const codes = [...(fixtureCodes[zoneId] ?? [])];
    return settle(
      { zone_id: zoneId, zone_code: zone?.code ?? zoneId, postal_codes: codes, total: codes.length },
      160,
    );
  }
  return apiRequest<ZonePostalCodesOut>(`/storefront/delivery/zones/${zoneId}/postal-codes`);
}

/**
 * The organization's whole map, postal code → owning zone.
 *
 * This is what makes "moving 11 codes out of Mumbai — suburbs" sayable *before* a write. There is
 * no single endpoint for it in the published schema, so it is assembled from one request per zone
 * — fine at the ADR's own worst case (§12: a metro chain runs twelve zones) and flagged in the
 * report as the one place a `GET /delivery/postal-codes` would collapse N requests into one.
 *
 * It is a snapshot. Another admin editing at the same moment makes it stale, which is why the
 * preview is labelled a preview and the server's own answer is what the screen reports afterwards.
 */
export async function loadPostalCodeMap(zones: readonly ZoneOut[]): Promise<PostalCodeMap> {
  const lists = await Promise.all(zones.map((zone) => listZonePostalCodes(zone.id)));
  const map = new Map<string, PostalCodeOwner>();
  zones.forEach((zone, index) => {
    for (const code of lists[index]?.postal_codes ?? []) {
      map.set(code, { zoneId: zone.id, zoneCode: zone.code, zoneName: zone.name });
    }
  });
  return map;
}

/**
 * `PUT /delivery/zones/{id}/postal-codes` — **the raw text the merchant typed**, not a parsed list
 * (`ZonePostalCodesPut.postal_codes` is a `string`).
 *
 * That split is right: the client's parser exists to show a preview, and the server's is the one
 * that decides. Two parsers that must agree would drift; two where only one is authoritative
 * cannot. The response is the zone's resulting set, which is what the screen reports afterwards.
 */
export async function saveZonePostalCodes(
  zoneId: string,
  postalCodes: string,
): Promise<ZonePostalCodesOut> {
  if (USE_FIXTURES) {
    const parsed = parsePostalCodeText(postalCodes);
    const codes = [...new Set(parsed.codes.map((code) => code.value))];
    // A code moving in leaves its old zone — the unique-per-organization rule (ADR-0010 §2).
    for (const code of codes) {
      const owner = fixtureOwnerOf(code);
      if (owner !== null && owner !== zoneId) {
        fixtureCodes = {
          ...fixtureCodes,
          [owner]: (fixtureCodes[owner] ?? []).filter((held) => held !== code),
        };
      }
    }
    fixtureCodes = { ...fixtureCodes, [zoneId]: codes };
    const zone = fixtureZones.find((candidate) => candidate.id === zoneId);
    return settle(
      { zone_id: zoneId, zone_code: zone?.code ?? zoneId, postal_codes: codes, total: codes.length },
      400,
    );
  }
  return apiRequest<ZonePostalCodesOut>(`/storefront/delivery/zones/${zoneId}/postal-codes`, {
    method: "PUT",
    body: { postal_codes: postalCodes },
  });
}

/**
 * `POST /delivery/postal-codes/import` → `ImportResultOut` (ADR-0010 §9).
 *
 * `text/csv`, not JSON, which is why it does not go through `apiRequest` — that helper JSON-encodes
 * its body. `uploadAsset` sets the same precedent for the one other call in this app that does not.
 * §9's size arithmetic puts a 19,000-row file at ~230 KB, comfortably inside the gateway's 2 MiB
 * default, so there is no chunking and no `max_body_bytes` override.
 */
export async function importPostalCodes(
  csv: string,
  mode: ImportMode,
): Promise<ImportResultOut> {
  if (USE_FIXTURES) {
    // Fixture mode applies the file so the merchant sees the map actually change, and counts the
    // same five things the server's audit row counts.
    const parsed = parseImportCsv(csv);
    if (mode === "replace") {
      fixtureCodes = Object.fromEntries(Object.keys(fixtureCodes).map((id) => [id, []]));
    }
    let inserted = 0;
    let updated = 0;
    const before = new Set(Object.values(fixtureCodes).flat());
    for (const row of parsed.rows) {
      const zone = fixtureZones.find((candidate) => candidate.code === row.zoneCode);
      if (!zone) continue;
      const owner = fixtureOwnerOf(row.postalCode);
      if (owner === zone.id) continue;
      if (owner !== null) {
        fixtureCodes = {
          ...fixtureCodes,
          [owner]: (fixtureCodes[owner] ?? []).filter((held) => held !== row.postalCode),
        };
      }
      fixtureCodes = {
        ...fixtureCodes,
        [zone.id]: [...(fixtureCodes[zone.id] ?? []), row.postalCode],
      };
      if (before.has(row.postalCode)) updated += 1;
      else inserted += 1;
    }
    const after = new Set(Object.values(fixtureCodes).flat());
    const deleted = [...before].filter((code) => !after.has(code)).length;
    return settle(
      {
        mode,
        rows_read: parsed.rows.length + parsed.rejected.length + parsed.malformed.length,
        inserted,
        updated,
        deleted,
        rejected: parsed.rejected.length,
      },
      600,
    );
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "text/csv",
  };
  const token = getAccessToken();
  if (token !== null) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}/storefront/delivery/postal-codes/import?mode=${mode}`, {
    method: "POST",
    headers,
    body: csv,
  });

  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) {
    const problem = await res
      .json()
      .then((body: ProblemJson) => body)
      .catch(() => null);
    throw new ApiError(res.status, problem?.detail ?? `The import failed (${res.status}).`, problem);
  }
  return (await res.json()) as ImportResultOut;
}

/* ---------------------------------- check ------------------------------------ */

/**
 * The tester — `POST /delivery/check` → `MerchantServiceabilityOut` (ADR-0010 §9).
 *
 * "Same selector, same response shape, plus the matched zone id" as the buyer's public route. The
 * client computes nothing: it sends a postal code and renders what comes back, which is the whole
 * reason this endpoint exists rather than a second implementation of the rules in the admin.
 *
 * It throws on every failure. Deciding what a failure *means* is `classifyCheckFailure`'s job, made
 * once, because getting it wrong is how a transient 503 becomes "your store doesn't deliver to your
 * own warehouse".
 */
export async function checkServiceability(
  postalCode: string,
): Promise<MerchantServiceabilityOut> {
  if (USE_FIXTURES) {
    return withTimeout(settle(fixtureAnswer(postalCode), 420), CHECK_TIMEOUT_MS);
  }
  return withTimeout(
    apiRequest<MerchantServiceabilityOut>("/storefront/delivery/check", {
      method: "POST",
      body: { postal_code: postalCode },
    }),
    CHECK_TIMEOUT_MS,
  );
}

/**
 * What the server would answer, for fixture mode.
 *
 * Built from the same three facts the server's selector uses — is there a mapping, is the zone
 * active, what is its mode — so the states a merchant sees offline are the states that exist.
 */
function fixtureAnswer(postalCode: string): MerchantServiceabilityOut {
  const owner = fixtureOwnerOf(postalCode);
  const zone = owner === null ? undefined : fixtureZones.find((candidate) => candidate.id === owner);
  const base = {
    postal_code: postalCode,
    checked_at: new Date().toISOString(),
    zone_id: zone?.id ?? null,
    zone_code: zone?.code ?? null,
    zone_name: zone?.name ?? null,
  };

  if (!zone) {
    return {
      ...base,
      serviceable: false,
      mode: "UNSERVED",
      reason: "no_mapping",
      promise: null,
      fees: null,
      buyer_note: null,
    };
  }
  if (!zone.is_active || zone.mode === "UNSERVED") {
    return {
      ...base,
      serviceable: false,
      mode: zone.mode,
      reason: zone.is_active ? "excluded" : "zone_inactive",
      promise: null,
      fees: null,
      buyer_note: zone.buyer_note,
    };
  }

  // The server resolves the date in the store's timezone (§3). Fixture mode adds the offset to
  // today so the shape is right; the wording is still the client's.
  const date =
    zone.promise_kind === "DATE_OFFSET" && zone.promise_offset_days !== null
      ? new Date(Date.now() + zone.promise_offset_days * 86_400_000).toISOString().slice(0, 10)
      : null;

  return {
    ...base,
    serviceable: true,
    mode: zone.mode,
    reason: null,
    promise:
      zone.promise_kind === null
        ? null
        : { kind: zone.promise_kind, minutes: zone.promise_minutes, date },
    fees: {
      currency: FIXTURE_STORE.currency,
      delivery_fee_minor: zone.delivery_fee_minor,
      free_delivery_above_minor: zone.free_delivery_above_minor,
      min_order_minor: zone.min_order_minor,
      display_only: true,
    },
    buyer_note: zone.buyer_note,
  };
}

/**
 * Why a check did not produce an answer — **never** "not serviceable".
 *
 * ADR-0010 §13's governing rule: *unknown is not no*. The server never returns an `UNSERVED`
 * outcome it did not compute, so a thrown error is always a transport fact and is rendered as one.
 *
 * Three statuses are **not** unknown and are handed back for the screen to render properly: a 403
 * is a permission fact, a 402 is a plan fact, and a 422 is something wrong with what was typed.
 */
export type CheckFailure =
  | { kind: "unknown"; detail: string; reference: string | null }
  | { kind: "denied"; detail: string }
  | { kind: "plan"; detail: string }
  | { kind: "invalid"; detail: string };

export function classifyCheckFailure(error: unknown): CheckFailure {
  if (error instanceof TimeoutError) {
    return {
      kind: "unknown",
      detail:
        "The check took too long to answer. Nothing is wrong with your zones — try again in a moment.",
      reference: null,
    };
  }
  if (error instanceof ApiError) {
    const reference = error.problem?.request_id ?? null;
    if (error.status === 403) {
      return {
        kind: "denied",
        detail:
          "You don't have permission to check serviceability. Ask an owner for storefront.delivery.zone.read.",
      };
    }
    if (error.status === 402) {
      return {
        kind: "plan",
        detail: "Storefront isn't on this organization's plan, so delivery is switched off.",
      };
    }
    if (error.status === 422 || error.status === 400) {
      return { kind: "invalid", detail: error.message };
    }
    if (error.status === 429) {
      return {
        kind: "unknown",
        detail: "Too many checks in a row. Wait a few seconds and try again.",
        reference,
      };
    }
    return {
      kind: "unknown",
      detail:
        error.status >= 500
          ? "The storefront service didn't answer. This says nothing about whether you deliver there — try again."
          : `The check couldn't be completed (${error.status}).`,
      reference,
    };
  }
  return {
    kind: "unknown",
    detail:
      "Couldn't reach the server. Check your connection and try again — this says nothing about whether you deliver there.",
    reference: null,
  };
}

/* ---------------------------------- demand ----------------------------------- */

export interface DemandReport {
  rows: DemandTotal[];
  /** True when the page cap stopped the walk, so the totals are a floor rather than the truth. */
  truncated: boolean;
  /** Counted rows the server says exist in the range, whether or not they were all fetched. */
  total: number;
}

/**
 * Sum the daily counter rows into one row per postal code and outcome, most-asked first.
 *
 * ADR-0010 §7 stores a counter per `(check_date, postal_code, outcome)` and the endpoint returns it
 * that way. The merchant's question is about the range, not the day, so the summing happens here.
 * Exported because it is the part worth testing.
 */
export function aggregateDemand(rows: readonly DemandRowOut[]): DemandTotal[] {
  const totals = new Map<string, DemandTotal>();
  for (const row of rows) {
    const key = `${row.postal_code} ${row.outcome}`;
    const found = totals.get(key);
    if (found) {
      found.checks += row.checks;
      if (row.check_date > found.last_checked_on) found.last_checked_on = row.check_date;
    } else {
      totals.set(key, {
        postal_code: row.postal_code,
        outcome: row.outcome,
        checks: row.checks,
        last_checked_on: row.check_date,
      });
    }
  }
  return [...totals.values()].sort((a, b) => b.checks - a.checks);
}

/**
 * `GET /delivery/demand?since=&until=` → `Page_DemandRowOut_`, walked and summed.
 *
 * Folded into `storefront.delivery.zone.read` rather than given a fifth permission (§9): anyone who
 * can see the zones can see which postal codes were turned away.
 */
export async function getDemand(since: string, until: string): Promise<DemandReport> {
  if (USE_FIXTURES) {
    const rows = FIXTURE_DEMAND.map((row) => ({ ...row }));
    return settle(
      { rows: aggregateDemand(rows), truncated: false, total: rows.length },
      260,
    );
  }

  const collected: DemandRowOut[] = [];
  let page = 1;
  let pages = 1;
  let total = 0;
  while (page <= pages && page <= DEMAND_PAGE_CAP) {
    const body = await apiRequest<Page<DemandRowOut>>(
      `/storefront/delivery/demand?since=${encodeURIComponent(since)}&until=${encodeURIComponent(until)}&page=${page}&size=${PAGE_SIZE}`,
    );
    collected.push(...body.items);
    pages = body.pages;
    total = body.total;
    page += 1;
  }

  return { rows: aggregateDemand(collected), truncated: pages > DEMAND_PAGE_CAP, total };
}

/** Put the fixture map back to its seed. Test-only; live mode holds no state to reset. */
export function resetDeliveryFixtures(): void {
  fixtureZones = FIXTURE_ZONES.map((zone) => ({ ...zone }));
  fixtureCodes = Object.fromEntries(
    Object.entries(FIXTURE_POSTAL_CODES).map(([id, codes]) => [id, [...codes]]),
  );
}
