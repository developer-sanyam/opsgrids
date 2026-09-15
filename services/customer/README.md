# Customer service

The account-book half of the **CRM** product ("OpsGrids Revenue"). Owns the `customer`
schema: the rep directory (owners), companies and contacts. Serves the `product-crm` SPA
together with deal-service, which owns the pipeline and points at these rows by plain
UUID — never a cross-schema foreign key.

```bash
make run SERVICE=customer PORT=8130    # API (the gateway routes /api/v1/customers and
                                       # /api/v1/contacts here)
make worker SERVICE=customer           # event consumer (none registered this slice)
make relay SERVICE=customer            # outbox relay
```

## Scope of this slice — reads first

The CRM SPA is read-only today (no create affordances), so the HTTP surface is the reads
it renders. The write path (`services.create_owner/create_company/create_contact`) exists
to finalise the domain rules and to drive the demo seed through the real, RLS-bound path;
it is not yet exposed as public routes (the finance-service precedent).

## Owns

- **`owners`** — the rep directory: the sales people every `owner_id` in the product
  resolves to, including deal-service's. Reference data within the organization.
- **`companies`** — the accounts (name, industry, size band, city, status, owning rep).
  `annual_revenue_minor` is integer minor units + ISO currency; no floats.
- **`contacts`** — the people at those accounts, cross-linked to their company by a real
  in-schema FK. `phone` holds the **masked display form only** — this slice never accepts
  or stores a raw number (employee-service's slice-1 precedent: no PII outside the sealed
  envelope, so none is held at all). `last_activity_at` is maintained from the pipeline's
  timeline.

Every table carries `organization_id` with an RLS policy (enable + FORCE, USING and
WITH CHECK on the tenant GUC); `processed_events` is deliberately unpolicied (no
organization column — a policy would silently defeat consumer idempotency).

## Endpoints

Match the SPA's `client.ts` call shapes (its `/companies` and `/owners` calls map onto
`/customers` and `/customers/owners` — the entity is the same, the gateway prefix is the
one already routed):

| Method & path | Returns | Permission |
| --- | --- | --- |
| `GET /customers?q=&status=` | `Page<Company>` | `crm.book.company.read` |
| `GET /customers/owners` | `Owner[]` | `crm.book.company.read` |
| `GET /customers/{id}` | `Company` | `crm.book.company.read` (+ object-level scope) |
| `GET /contacts?q=&status=` | `Page<Contact>` | `crm.book.contact.read` |
| `GET /contacts/{id}` | `Contact` | `crm.book.contact.read` (+ object-level scope) |

`q` searches name/industry/city (companies) and name/title/email (contacts) — the same
fields the SPA filters on. Every route also requires the `crm` product entitlement.

## Permissions and roles (`crm` product, book slice)

OWN means "the accounts I own" (the owner column is the rep id); TEAM and LOCATION are not
offered. This service declares the `crm.book.*` slice of the CRM vocabulary; deal-service
declares `crm.pipeline.*` in its own process — each process owns the catalogue for the
permissions it serves, exactly as the HRMS services do.

- `crm.book.company.read` / `crm.book.company.write`
- `crm.book.contact.read` / `crm.book.contact.write`

Role templates: **CRM_BOOK_ADMIN** (everything at ORG) and **CRM_SALES_REP** (default:
reads at ORG — sales works one shared book — writes at OWN).

## Publishes

- `crm.book.company.created` — a company joined the book.
- `crm.book.contact.created` — a person was added to an account.

## Consumes

Nothing yet. The pipeline's `crm.pipeline.activity.logged` is the natural feed for
`contacts.last_activity_at`; this slice seeds it directly and `touch_contact_activity`
is the consumer's write path when it lands.

## Demo seed

`tools/demo/seed_crm.py` seeds **both** CRM services and mirrors the SPA fixtures exactly
(6 owners, 15 companies, 18 contacts — plus the pipeline in deal-service) into the
"OpsGrids Demo" org via the real write paths, cross-linked by deterministic UUIDs.
Idempotent. `DATABASE_URL` must point at `opsgrids_wt`.
