/**
 * Sample delivery data for fixture mode.
 *
 * `VITE_USE_FIXTURES` defaults on in dev, so this is what the admin renders against with no
 * gateway and no login — exactly as the rest of this app renders from `src/api/fixtures.ts`. Every
 * screen that shows it wears the `SampleChip`, because a merchant must never mistake a sample zone
 * map for their own.
 *
 * Shapes are `ZoneOut`, `StoreOut` and `DemandRowOut` from the published schema, not invented ones,
 * so fixture mode exercises the same code paths the live gateway will.
 *
 * The shape of the data is chosen to exercise the states, not to look tidy: a duration promise, a
 * date-offset promise with a cutoff, a deliberately excluded zone, and an inactive one — which is
 * the only way `zone_inactive` is ever seen.
 */

import type { DemandRowOut, StoreOut, ZoneOut } from "./types";

export const FIXTURE_STORE: StoreOut = {
  id: "store-fixture-0001",
  slug: "greenleaf",
  status: "published",
  currency: "INR",
  timezone: "Asia/Kolkata",
  custom_domain: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

export const FIXTURE_ZONES: ZoneOut[] = [
  {
    id: "zone-central",
    code: "MUM-CENTRAL",
    name: "Mumbai — central",
    mode: "PROMISED",
    promise_kind: "DURATION",
    promise_minutes: 30,
    promise_offset_days: null,
    promise_cutoff_time: null,
    delivery_fee_minor: 2900,
    free_delivery_above_minor: 49900,
    min_order_minor: 19900,
    is_active: true,
    buyer_note: "Riders are on the road from 7am to 11pm.",
    postal_code_count: 4,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  },
  {
    id: "zone-suburbs",
    code: "MUM-SUB",
    name: "Mumbai — suburbs",
    mode: "PROMISED",
    promise_kind: "DURATION",
    promise_minutes: 75,
    promise_offset_days: null,
    promise_cutoff_time: null,
    delivery_fee_minor: 4900,
    free_delivery_above_minor: 99900,
    min_order_minor: 29900,
    is_active: true,
    buyer_note: null,
    postal_code_count: 3,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  },
  {
    id: "zone-nashik",
    code: "NASHIK",
    name: "Nashik & around",
    mode: "PROMISED",
    promise_kind: "DATE_OFFSET",
    promise_minutes: null,
    promise_offset_days: 2,
    promise_cutoff_time: "16:00",
    delivery_fee_minor: 9900,
    free_delivery_above_minor: null,
    min_order_minor: 99900,
    is_active: true,
    buyer_note: "Courier delivery, signature required.",
    postal_code_count: 3,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  },
  {
    id: "zone-pune",
    code: "PUNE-PILOT",
    name: "Pune — pilot",
    mode: "PROMISED",
    promise_kind: "DURATION",
    promise_minutes: 90,
    promise_offset_days: null,
    promise_cutoff_time: null,
    delivery_fee_minor: 4900,
    free_delivery_above_minor: null,
    min_order_minor: 49900,
    is_active: false,
    buyer_note: null,
    postal_code_count: 2,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  },
  {
    id: "zone-excluded",
    code: "OUT-OF-RANGE",
    name: "Mapped and excluded",
    mode: "UNSERVED",
    promise_kind: null,
    promise_minutes: null,
    promise_offset_days: null,
    promise_cutoff_time: null,
    delivery_fee_minor: 0,
    free_delivery_above_minor: null,
    min_order_minor: 0,
    is_active: true,
    buyer_note: "We don't have a courier partner here yet.",
    postal_code_count: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  },
];

/** zone id → the postal codes it holds. One code is in exactly one list, by construction. */
export const FIXTURE_POSTAL_CODES: Record<string, string[]> = {
  "zone-central": ["400050", "400051", "400052", "400053"],
  "zone-suburbs": ["400059", "400069", "400072"],
  "zone-nashik": ["422001", "422002", "422003"],
  "zone-pune": ["411001", "411002"],
  "zone-excluded": ["194101"],
};

/**
 * Demand, most-asked first. The point of the report: four of these are postal codes nobody has
 * mapped, and the top one has been asked for 34 times this month by people who were told no.
 */
export const FIXTURE_DEMAND: DemandRowOut[] = [
  { postal_code: "421201", outcome: "NO_MAPPING", checks: 34, check_date: "2026-09-18" },
  { postal_code: "400703", outcome: "NO_MAPPING", checks: 21, check_date: "2026-09-19" },
  { postal_code: "411045", outcome: "NO_MAPPING", checks: 16, check_date: "2026-09-17" },
  { postal_code: "411001", outcome: "ZONE_INACTIVE", checks: 12, check_date: "2026-09-19" },
  { postal_code: "401107", outcome: "NO_MAPPING", checks: 9, check_date: "2026-09-12" },
  { postal_code: "194101", outcome: "EXCLUDED", checks: 4, check_date: "2026-09-08" },
  { postal_code: "400050", outcome: "SERVED", checks: 288, check_date: "2026-09-19" },
  { postal_code: "400072", outcome: "SERVED", checks: 154, check_date: "2026-09-19" },
];
