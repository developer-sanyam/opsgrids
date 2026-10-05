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
- **`plan_versions`** — an immutable, priced snapshot a subscription pins. `draft -> published
  -> grandfathered`; once `published` the row is frozen by a `BEFORE UPDATE` trigger and a service
  guard, which allow exactly one change — withdraw, `state` to `grandfathered` with every other
  column identical — and refuse any update of a grandfathered row (migration `b7e4d2a9c613`). A
  draft may be deleted; nothing published ever is. A nullable
  **`restricted_to_org_id`** makes a version bespoke — an audience of one — for negotiated
  pricing (ADR-0004 §2); a NULL is a public version.
- **`prices`** — a priced component (`flat` / `per_seat` / `usage`). Money is integer minor
  units; a usage rate is integer micros of a major unit (ADR-0000 §8). No floats, no `Numeric`.
- **`features`** — capability flags/limits a version switches on, keyed by the product's own
  feature names, value as JSONB. Three keys are read by the *platform* rather than a product:
  `bundled_products` and `bundle_tier` declare a bundle, and `partner_of` labels a reseller price.
- **`seat_classes`** — the classes of seat a version sells: a `code`, a nullable `price_id` (NULL
  is a **free** class, e.g. a view-only seat), an `included_count`, and `can_write`. Frozen with
  its version by the same trigger that freezes prices, because what a seat may do is a term of the
  deal. `domain.seat_class_grants` derives the permission set: a `can_write=False` class can only
  ever produce non-dangerous read grants.

These five are **global and un-policied** (ADR-0002 §5): they carry no `organization_id` and
take no RLS policy. Bespoke visibility is an application filter
(`restricted_to_org_id IS NULL OR = principal.organization_id`), defended by a negative-authz
test — a global table cannot lean on row-level security. Only `audit_logs` and `outbox` are
tenant-policied, bound to the platform sentinel organization that catalog's own platform-level
rows belong to — and `idempotency_keys`, the claim table behind the required `Idempotency-Key` on
every operator write (unique `(organization_id, key)`, the request fingerprint and the response to
replay), policied the same way.

## Publishes

- **`catalog.plan_version.published`** — a version is now frozen and offerable. Payload carries
  the plan, product, version, currency, interval, `restricted_to_org_id`, and `published_at`.
- **`catalog.plan_version.grandfathered`** — a published version was withdrawn: existing pins keep
  it, nothing new may pin it. Same payload shape plus `withdrawn_at`.
- **`catalog.plan.retired`** — a plan is no longer offered (its pinned versions stay
  reconstructable).
- **`catalog.plan.reactivated`** — a retired plan is offered again.

## Consumes

_None yet._

## Packaging

Bundles, prepaid packs, seat classes and reseller pricing are written up in
[docs/commerce-packaging.md](../../docs/commerce-packaging.md). Three of the four are pure
configuration over the tables above — a **bundle** is a plan version whose features name its
members and its rung, and a **partner price** is `restricted_to_org_id` pointed at the reseller,
the same audience-of-one mechanism a negotiated enterprise price uses. `seat_classes` is the one
that needed a table. Migration `f78c86fcec78` seeds a graduated ladder (`suite/duo`, `trio`,
`full`) whose monotonicity is checked by a test against the seed's own numbers. Authoring a
version that declares `bundled_products` asks product-service's registry
(`GET /internal/v1/products`, `PRODUCT_SERVICE_URL`) whether every member is a real, non-platform,
non-bundle product and refuses the draft otherwise (503 if the registry cannot be reached).

## Surfaces

- **Customer** (product gateway): `GET /api/v1/plans?product_key=`, `GET /api/v1/catalog/plans/{id}`
  — published versions the caller may see, behind `core.product.catalogue.read`.
- **Operator** (operator gateway, `staff_only`, ADR-0029). Reads, `owner` and `billing_ops` (not
  `support` — bespoke prices are negotiated prices): `GET /api/v1/admin/catalog/plans`
  (`product_key`, `audience=public|restricted|all` default `public`, `state`, keyset `cursor`,
  `limit`), `GET .../plans/{id}` (version summaries, paged), `GET .../versions/{id}` (the full
  sheet, prices in a deterministic order, with ids). Writes, `owner` only, each with a `reason`
  and a **required** `Idempotency-Key`: `POST`/`PATCH .../plans` (product key checked against the
  registry), `POST .../plans/{id}/retire`, `.../reactivate`, `POST .../plans/{id}/versions` (a full
  sheet; bespoke when `restricted_to_org_id` is set; a lost version-number race retries in a
  savepoint), `POST .../versions/{id}/publish` (validated first — see below; `allow_zero_amounts`
  to publish a deliberate zero), `POST .../versions/{id}/withdraw`, `DELETE .../versions/{id}`
  (drafts only). Refused with 404, not 403, to any non-operator token.

Publishing refuses, all at once as `errors[]`: no prices; a currency outside
`domain.SELLABLE_CURRENCIES`; a usage price with no `meter_key` or rate; a per-seat price with no
unit; a zero amount without `allow_zero_amounts`; an invalid `trial_days`; and bundle members
today's registry no longer vouches for.
