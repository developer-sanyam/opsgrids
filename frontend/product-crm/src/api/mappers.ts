/**
 * The one seam where a wire row from the gateway becomes this product's domain type. A
 * "company" in this product is a `customer` row in the customer service — same account, this
 * product's vocabulary for it — so `companyFromWire` is the explicit, typed place that mapping
 * happens, rather than an assumption every screen makes on its own.
 */
import type { Activity, Company, Contact, Deal, Owner } from "./types";
import type { ActivityOut, CompanyOut, ContactOut, DealOut, OwnerOut, WirePage } from "./wire";

export function ownerFromWire(row: OwnerOut): Owner {
  return {
    id: row.id,
    full_name: row.full_name,
    email: row.email,
    role: row.role,
  };
}

/** `CompanyOut` is the customer service's `customer` row — this product's "company". */
export function companyFromWire(row: CompanyOut): Company {
  return {
    id: row.id,
    name: row.name,
    domain: row.domain,
    industry: row.industry,
    size_band: row.size_band,
    city: row.city,
    country: row.country,
    status: row.status,
    owner_id: row.owner_id,
    annual_revenue_minor: row.annual_revenue_minor,
    currency: row.currency,
    created_at: row.created_at,
  };
}

export function contactFromWire(row: ContactOut): Contact {
  return {
    id: row.id,
    full_name: row.full_name,
    title: row.title,
    email: row.email,
    phone: row.phone,
    company_id: row.company_id,
    status: row.status,
    owner_id: row.owner_id,
    last_activity_at: row.last_activity_at,
    created_at: row.created_at,
  };
}

export function dealFromWire(row: DealOut): Deal {
  return {
    id: row.id,
    name: row.name,
    company_id: row.company_id,
    primary_contact_id: row.primary_contact_id,
    value_minor: row.value_minor,
    currency: row.currency,
    stage: row.stage,
    owner_id: row.owner_id,
    probability: row.probability,
    close_date: row.close_date,
    created_at: row.created_at,
    last_activity_at: row.last_activity_at,
    source: row.source,
  };
}

export function activityFromWire(row: ActivityOut): Activity {
  return {
    id: row.id,
    type: row.type,
    subject: row.subject,
    body: row.body,
    deal_id: row.deal_id,
    company_id: row.company_id,
    contact_id: row.contact_id,
    owner_id: row.owner_id,
    occurred_at: row.occurred_at,
    direction: row.direction,
  };
}

/** Maps a gateway page envelope down to this product's small `{ items, total }` envelope. */
export function mapPage<TWire, TUi>(
  page: WirePage<TWire>,
  mapItem: (row: TWire) => TUi,
): { items: TUi[]; total: number } {
  return { items: page.items.map(mapItem), total: page.total };
}
