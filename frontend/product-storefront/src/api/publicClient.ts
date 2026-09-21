/**
 * The anonymous buyer's calls — serviceability, the public catalogue and the enquiry.
 *
 * **Nothing here imports `@opsgrids/client`, the theme library or the fixture catalogue.** Those
 * three are exactly what `src/api/client.ts`'s own header comment says the buyer bundle must
 * never carry: `@opsgrids/client` brings the session and the auth machinery, which cannot work on
 * a merchant's own domain (ADR-0001 scopes the refresh cookie to `.opsgrids.com`) and is dead
 * weight with an XSS surface and nothing to gain; the theme library and the fixture catalogue are
 * console-only. This module exists because `theme/render/EnquiryForm.tsx` and
 * `theme/render/DeliveryCheck.tsx` (through `lib/serviceability.ts`) are rendered by
 * `StoreRenderer`, which both the console preview *and* the real buyer bundle mount — so whatever
 * they call has to be safe for both, and `src/api/client.ts` is not.
 *
 * Pattern is `storeContext.ts`'s: hand-rolled `fetch`, a same-origin base declared here rather
 * than imported, and every failure resolved to a typed error the caller renders.
 */

import { STOREFRONT_API_BASE } from "./storeContext";

/** A failed public request. No session, so there is nothing to sign anybody out of. */
export class PublicApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "PublicApiError";
    this.status = status;
  }
}

interface ProblemBody {
  detail?: string;
}

async function readDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as ProblemBody;
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

/* ----------------------------- serviceability ----------------------------- */

export type ZoneMode = "PROMISED" | "SLOTTED" | "UNSERVED";
export type PromiseKind = "DURATION" | "DATE_OFFSET";
export type UnservedReason = "no_mapping" | "excluded" | "zone_inactive";

export interface PromiseOut {
  kind: PromiseKind;
  minutes?: number | null;
  date?: string | null;
}

export interface FeesOut {
  currency: string;
  delivery_fee_minor: number;
  free_delivery_above_minor?: number | null;
  min_order_minor?: number;
  display_only?: boolean;
}

export interface ServiceabilityOut {
  postal_code: string;
  serviceable: boolean;
  mode: ZoneMode;
  checked_at: string;
  reason?: UnservedReason | null;
  promise?: PromiseOut | null;
  fees?: FeesOut | null;
  buyer_note?: string | null;
}

export interface ServiceabilityRequest {
  store: string;
  postal_code: string;
}

/** Past the server's 2-second budget so a slow-but-working check is never cut off here first. */
const SERVICEABILITY_TIMEOUT_MS = 8_000;

/**
 * `POST /storefront/public/serviceability`. Every failure throws — the caller renders that as
 * "we couldn't check", never as "we don't deliver here" (ADR-0010 §13).
 */
export async function checkServiceability(input: ServiceabilityRequest): Promise<ServiceabilityOut> {
  const code = input.postal_code.toUpperCase().replace(/[^0-9A-Z]/g, "");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SERVICEABILITY_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${STOREFRONT_API_BASE}/storefront/public/serviceability`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ store: input.store, postal_code: code }),
      signal: controller.signal,
    });
  } catch {
    throw new PublicApiError(0, "Couldn't reach the delivery check.");
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    throw new PublicApiError(res.status, await readDetail(res, `The delivery check failed (${res.status}).`));
  }
  return (await res.json()) as ServiceabilityOut;
}

/* ------------------------------- catalogue -------------------------------- */

export interface ProductVariantPublic {
  id: string;
  label: string;
  price_min_minor: number;
  price_max_minor: number;
  mrp_minor: number | null;
  in_stock: boolean;
}

/** Field-for-field the console's `Product` (`src/api/types.ts`) minus merchant-only detail. */
export interface ProductPublic {
  id: string;
  code: string;
  name: string;
  brand: string;
  category: string;
  spec: string;
  unit: string;
  price_min_minor: number;
  price_max_minor: number;
  currency: string;
  gst_inclusive: boolean;
  moq: number;
  moq_unit: string;
  in_stock: boolean;
  lead_time_days: number;
  featured: boolean;
  image_url: string | null;
  mrp_minor: number | null;
  delivery_eta_minutes: number | null;
  variants: ProductVariantPublic[];
}

export interface CategoryPublic {
  key: string;
  label: string;
  blurb: string | null;
  image_url: string | null;
  line_count: number;
}

export interface PublicPage<T> {
  items: T[];
  total: number;
  page: number;
  pages: number;
  size: number;
}

export interface PublicCatalogue {
  categories: CategoryPublic[];
  products: PublicPage<ProductPublic>;
}

export interface PublicCatalogueRequest {
  store: string;
  page?: number;
  size?: number;
  category?: string;
}

/** `POST /storefront/public/catalogue`. `page`/`size` travel as query params, `store`/`category`
 *  in the body — the same split the route table's `PageQuery` dependency gives every list. */
export async function fetchPublicCatalogue(input: PublicCatalogueRequest): Promise<PublicCatalogue> {
  const params = new URLSearchParams();
  params.set("page", String(input.page ?? 1));
  params.set("size", String(input.size ?? 24));
  let res: Response;
  try {
    res = await fetch(`${STOREFRONT_API_BASE}/storefront/public/catalogue?${params.toString()}`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ store: input.store, category: input.category ?? null }),
    });
  } catch {
    throw new PublicApiError(0, "Couldn't reach the store.");
  }
  if (!res.ok) {
    throw new PublicApiError(res.status, await readDetail(res, `The catalogue didn't load (${res.status}).`));
  }
  return (await res.json()) as PublicCatalogue;
}

/* -------------------------------- enquiries ------------------------------- */

export interface EnquiryLineInput {
  product_id: string;
  name: string;
  quantity: number;
}

export interface EnquiryInput {
  store: string;
  name: string;
  company: string;
  email: string;
  phone: string;
  requirement: string;
  quantity: string;
  gstin: string;
  lines: EnquiryLineInput[];
}

export interface EnquiryReceipt {
  id: string;
  received_at: string;
}

/**
 * `POST /storefront/public/enquiry`. No token — the buyer is anonymous, and this is the buyer's
 * own terminus for the trade line list (`store/lineList.ts`'s "send this list as an enquiry").
 */
export async function submitEnquiry(input: EnquiryInput): Promise<EnquiryReceipt> {
  const missing =
    input.store.trim() === "" ||
    input.name.trim() === "" ||
    input.company.trim() === "" ||
    input.email.trim() === "" ||
    input.requirement.trim() === "";
  if (missing) throw new Error("Some required details are missing. Check the fields above.");

  let res: Response;
  try {
    res = await fetch(`${STOREFRONT_API_BASE}/storefront/public/enquiry`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        store: input.store,
        name: input.name.trim(),
        company: input.company.trim(),
        email: input.email.trim(),
        phone: input.phone.trim() === "" ? null : input.phone.trim(),
        gstin: input.gstin.trim() === "" ? null : input.gstin.trim(),
        requirement: input.requirement.trim(),
        quantity: input.quantity.trim(),
        lines: input.lines,
      }),
    });
  } catch {
    throw new PublicApiError(0, "Couldn't reach the server. Nothing was sent — try again.");
  }
  if (!res.ok) {
    throw new PublicApiError(res.status, await readDetail(res, `That didn't send (${res.status}).`));
  }
  return (await res.json()) as EnquiryReceipt;
}
