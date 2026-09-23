/**
 * The one seam where a wire row from the gateway becomes this product's domain type. A
 * "company" in this product is a `customer` row in the customer service — same account, this
 * product's vocabulary for it — so `companyFromWire` is the explicit, typed place that mapping
 * happens, rather than an assumption every screen makes on its own.
 */
import type { Activity, Company, Contact, Deal, Lead, Owner, Summary } from "./types";
import type {
  ActivityOut,
  CompanyOut,
  ContactOut,
  DealOut,
  LeadOut,
  OwnerOut,
  SummaryOut,
  WirePage,
} from "./wire";

export function ownerFromWire(row: OwnerOut): Owner {
  return {
    id: row.id,
    full_name: row.full_name,
    email: row.email,
    role: row.role,
    is_active: row.is_active,
  };
}

export function leadFromWire(row: LeadOut): Lead {
  return {
    id: row.id,
    full_name: row.full_name,
    company: row.company,
    email: row.email,
    phone: row.phone,
    source: row.source,
    status: row.status,
    owner_id: row.owner_id,
    notes: row.notes,
    converted_company_id: row.converted_company_id,
    converted_contact_id: row.converted_contact_id,
    converted_deal_id: row.converted_deal_id,
    converted_at: row.converted_at,
    created_at: row.created_at,
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

export function summaryFromWire(row: SummaryOut): Summary {
  return {
    pipeline_value_minor: row.pipeline_value_minor,
    weighted_pipeline_minor: row.weighted_pipeline_minor,
    open_deals: row.open_deals,
    win_rate_pct: row.win_rate_pct,
    won_value_minor: row.won_value_minor,
    activities_this_week: row.activities_this_week,
    currency: row.currency,
    trend: row.trend.map((t) => ({ label: t.label, value_minor: t.value_minor })),
    funnel: row.funnel.map((f) => ({
      stage: f.stage,
      label: f.label,
      count: f.count,
      value_minor: f.value_minor,
      pct: f.pct,
    })),
  };
}

/** Maps a gateway page envelope to this product's `Page<T>` — same shape, mapped items. */
export function mapPage<TWire, TUi>(
  page: WirePage<TWire>,
  mapItem: (row: TWire) => TUi,
): { items: TUi[]; total: number; page: number; size: number; pages: number } {
  return {
    items: page.items.map(mapItem),
    total: page.total,
    page: page.page,
    size: page.size,
    pages: page.pages,
  };
}
