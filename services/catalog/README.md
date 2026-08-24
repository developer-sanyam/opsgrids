# Catalog service

The definitional layer of commerce (ADR-0004 §1): **what can be sold**. Owns the `catalog`
schema. Global platform vocabulary, like `product` — read by everything downstream, authored
only by staff, and changing on a pricing cadence rather than a customer's lifecycle.

```bash
make run SERVICE=catalog PORT=8164   # API
make worker SERVICE=catalog          # event consumer (none registered yet)
make relay SERVICE=catalog           # outbox relay
```

## Owns

- **`plans`** — a named offering under one product, keyed by the `product_key` string (no
  cross-schema FK). Unique per `(product_key, code)`. Retired, never deleted.
- **`plan_versions`** — an immutable, priced snapshot a subscription pins. `draft -> published`;
  once `published` the row is frozen by a `BEFORE UPDATE` trigger and a service guard. A nullable
  **`restricted_to_org_id`** makes a version bespoke — an audience of one — for negotiated
  pricing (ADR-0004 §2); a NULL is a public version.
- **`prices`** — a priced component (`flat` / `per_seat` / `usage`). Money is integer minor
  units; a usage rate is integer micros of a major unit (ADR-0000 §8). No floats, no `Numeric`.
- **`features`** — capability flags/limits a version switches on, keyed by the product's own
  feature names, value as JSONB.

These four are **global and un-policied** (ADR-0002 §5): they carry no `organization_id` and
take no RLS policy. Bespoke visibility is an application filter
(`restricted_to_org_id IS NULL OR = principal.organization_id`), defended by a negative-authz
test — a global table cannot lean on row-level security. Only `audit_logs` and `outbox` are
tenant-policied, bound to the platform sentinel organization that catalog's own platform-level
rows belong to.

## Publishes

- **`catalog.plan_version.published`** — a version is now frozen and offerable. Payload carries
  the plan, product, version, currency, interval, `restricted_to_org_id`, and `published_at`.
- **`catalog.plan.retired`** — a plan is no longer offered (its pinned versions stay
  reconstructable).

## Consumes

_None yet._

## Surfaces

- **Customer** (product gateway): `GET /api/v1/plans?product_key=`, `GET /api/v1/catalog/plans/{id}`
  — published versions the caller may see, behind `core.product.catalogue.read`.
- **Operator** (operator gateway, `staff_only`): `POST`/`PATCH /api/v1/admin/catalog/plans`,
  `POST .../plans/{id}/retire`, `POST .../plans/{id}/versions` (clone-to-draft; bespoke when
  `restricted_to_org_id` is set), `POST .../versions/{id}/publish`. Refused with 404, not 403,
  to any non-operator token.
