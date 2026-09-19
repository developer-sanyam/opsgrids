/**
 * The delivery module's wire contract.
 *
 * **Read off `libs/contracts/openapi/storefront.json`, not guessed.** That file is the schema the
 * service publishes; every interface below is a hand-transcription of one of its components, named
 * after it, so a regenerated client is a rename rather than a redesign. Where the schema and
 * ADR-0010's prose disagree, the schema wins and the disagreement is noted — those notes are the
 * list to take to the service owner.
 *
 * Money is integer minor units against an ISO currency (ADR-0000 §8). Nothing here is computed by
 * the client: a serviceability answer is produced only by the server's selector (ADR-0010 §5).
 */

/** ADR-0010 §3. `SLOTTED` is in the enum from day one and refused by the writer until slice 2. */
export type ZoneMode = "PROMISED" | "SLOTTED" | "UNSERVED";

/** ADR-0010 §3. Only meaningful when `mode` is `PROMISED`. */
export type PromiseKind = "DURATION" | "DATE_OFFSET";

/**
 * Why a postal code is not served. ADR-0010 §3 names exactly these three and says they are
 * distinct facts on purpose. The schema types `reason` as a plain `string`, so every read of it
 * goes through `reasonLabel`, which shows an unrecognised value rather than blanking it.
 */
export type UnservedReason = "no_mapping" | "excluded" | "zone_inactive";

/** The paged envelope every list endpoint returns (`Page_ZoneOut_`, `Page_DemandRowOut_`). */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pages: number;
  size: number;
}

/**
 * `StoreOut` — the store's identity row (ADR-0010 §1).
 *
 * **Contract collision worth escalating:** `src/api/client.ts`'s `getStore()` calls this same
 * `/storefront/store` and expects the theme document (`StoreDoc`) back. The published schema says
 * the path returns this. One of the two has to move, and it is not this one — this matches what
 * the service actually serves.
 */
export interface StoreOut {
  id: string;
  slug: string;
  status: "draft" | "published" | "suspended";
  /** ISO-4217. Every minor-unit amount in this module is denominated in it. */
  currency: string;
  /** IANA. A `DATE_OFFSET` cutoff is a wall clock in this zone, never the browser's. */
  timezone: string;
  custom_domain: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * `ZoneOut` — a zone is a name, a mode, and the behaviour applied to every postal code in it
 * (ADR-0010 §2, §10).
 *
 * **`delivery_fee_minor` and `min_order_minor` are non-nullable integers**, so "no fee" is `0`,
 * not `null`. Only `free_delivery_above_minor` is nullable, and there `null` means "no threshold"
 * rather than "a threshold of zero" — which would mean everything ships free.
 */
export interface ZoneOut {
  id: string;
  /** Unique per organization. The merchant's handle, and what the CSV import's `zone_code` names. */
  code: string;
  name: string;
  mode: ZoneMode;
  promise_kind: PromiseKind | null;
  /** `DURATION`: "in about 30 minutes". */
  promise_minutes: number | null;
  /** `DATE_OFFSET`: today + offset, or one more day after the cutoff. */
  promise_offset_days: number | null;
  /** `HH:MM`, a wall clock in the store's timezone. */
  promise_cutoff_time: string | null;
  /** Display only until slice 4 — there is no pricing engine (ADR-0010 §8). */
  delivery_fee_minor: number;
  free_delivery_above_minor: number | null;
  min_order_minor: number;
  is_active: boolean;
  buyer_note: string | null;
  /** Not `required` in the schema, so it may be absent. `zoneCodeCount` reads it defensively. */
  postal_code_count?: number;
  created_at: string;
  updated_at: string;
}

/**
 * `ZoneCreate`. Only `code`, `name` and `mode` are required; the money fields carry server-side
 * defaults, so a field the merchant left blank is **omitted** rather than sent as `null` — the
 * schema types them as plain integers and `null` is a 422.
 */
export interface ZoneCreate {
  code: string;
  name: string;
  mode: ZoneMode;
  promise_kind?: PromiseKind | null;
  promise_minutes?: number | null;
  promise_offset_days?: number | null;
  promise_cutoff_time?: string | null;
  delivery_fee_minor?: number;
  free_delivery_above_minor?: number | null;
  min_order_minor?: number;
  is_active?: boolean;
  buyer_note?: string | null;
}

/**
 * `ZonePatch` — every field optional, and an omitted field means "leave it alone".
 *
 * So an edit sends **every** field the form owns, including `0` for a fee the merchant cleared.
 * Omitting it would silently keep the old amount, which is the shape of bug where a merchant
 * deletes a fee, sees it disappear from the form, and watches it still print on the buyer's card.
 */
export type ZonePatch = ZoneCreate;

/** `PromiseOut` — a count or a resolved date, never prose (ADR-0010 §3). */
export interface PromiseOut {
  kind: PromiseKind;
  minutes: number | null;
  /** ISO `YYYY-MM-DD`, already resolved in the store's timezone. */
  date: string | null;
}

/**
 * `FeesOut`.
 *
 * **`display_only` is the server saying it out loud**, and it is the flag this UI's fee caveat is
 * driven from rather than a hard-coded sentence — when slice 4 lands and the pricing engine owns
 * the charge, the screen stops claiming "not charged" without a frontend change.
 */
export interface FeesOut {
  currency: string;
  delivery_fee_minor: number;
  free_delivery_above_minor: number | null;
  min_order_minor: number;
  display_only?: boolean;
}

/**
 * `MerchantServiceabilityOut` — what `POST /delivery/check` answers.
 *
 * Same selector, same shape as the buyer's `ServiceabilityOut`, plus the matched zone's id, code
 * and name (ADR-0010 §9).
 *
 * **There is no `UNKNOWN` here, deliberately** (§13). The server never returns an outcome it did
 * not compute; unknown is a client-side state produced by a transport failure, and it lives in
 * `CheckState` below rather than on the wire.
 */
export interface MerchantServiceabilityOut {
  postal_code: string;
  serviceable: boolean;
  /** `PROMISED` | `SLOTTED` | `UNSERVED`. Typed loosely because the schema types it `string`. */
  mode: string;
  reason: string | null;
  promise: PromiseOut | null;
  fees: FeesOut | null;
  buyer_note: string | null;
  checked_at: string;
  zone_id: string | null;
  zone_code: string | null;
  zone_name: string | null;
}

/**
 * The tester's four states, which are the buyer's four states (ADR-0010 §15) minus the slot picker
 * that arrives in slice 2.
 *
 * `unknown` is first-class and is what every transport failure resolves to — never "not served".
 * Telling a merchant their own store will not deliver to their own warehouse because Redis blinked
 * is the exact defect §13 exists to prevent.
 */
export type CheckState =
  | { status: "idle" }
  | { status: "checking"; postal_code: string }
  | { status: "answered"; answer: MerchantServiceabilityOut }
  | { status: "unknown"; postal_code: string; detail: string; reference: string | null };

/** `ZonePostalCodesOut` — the zone's whole set, after a read or a write. */
export interface ZonePostalCodesOut {
  zone_id: string;
  zone_code: string;
  postal_codes: string[];
  total: number;
}

/**
 * `ZonePostalCodesPut` — **`postal_codes` is one string, not a list.**
 *
 * The raw text the merchant typed goes over the wire and the server does its own parsing,
 * normalising and de-duplication. That is the right split: the client's parse exists to *preview*
 * the change, and the server's is the one that decides. Two parsers that must agree would drift;
 * two parsers where only one is authoritative cannot.
 */
export interface ZonePostalCodesPut {
  postal_codes: string;
}

/**
 * `ImportResultOut` — ADR-0010 §9's five counters, as counters.
 *
 * **`rejected` is a count, not a list.** §9's prose says "offending line numbers in the response
 * body"; the schema does not carry them. Until it does, the line numbers a merchant sees come from
 * the client's own pre-flight plan, and this screen says which is which.
 */
export interface ImportResultOut {
  mode: string;
  rows_read: number;
  inserted: number;
  updated: number;
  deleted: number;
  rejected?: number;
}

/**
 * `DemandRowOut` — **one row per postal code per day per outcome**, not a range aggregate
 * (ADR-0010 §7's counter table, read back raw).
 *
 * The report a merchant wants is "most-asked first across the range", so the aggregation happens
 * in `aggregateDemand`. Doing it client-side is the honest option while the endpoint returns days:
 * summing here is exact, and a server-side `GROUP BY` would be better and is worth asking for.
 */
export interface DemandRowOut {
  postal_code: string;
  outcome: string;
  checks: number;
  /** ISO `YYYY-MM-DD`. */
  check_date: string;
}

/** One postal code, summed across the range. What the report's rows actually are. */
export interface DemandTotal {
  postal_code: string;
  outcome: string;
  checks: number;
  last_checked_on: string;
}

/** ADR-0010 §9: `merge` upserts (the default), `replace` clears the organization's map first. */
export type ImportMode = "merge" | "replace";

/** `postal_code_count` is not `required` in the schema, so it is read rather than assumed. */
export function zoneCodeCount(zone: ZoneOut): number | null {
  return zone.postal_code_count ?? null;
}
