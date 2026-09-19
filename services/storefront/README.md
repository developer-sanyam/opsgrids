# Storefront service

Part of the OpsGrids platform. Owns the `storefront` schema, port **8174**, product key
**`storefront`**. Built by [ADR-0010](../../docs/adr/0010-delivery-serviceability-and-slots.md),
slice 1.

```bash
make run SERVICE=storefront PORT=8174    # API
make worker SERVICE=storefront           # event consumer (no handlers yet)
make relay SERVICE=storefront            # outbox relay (the outbox is empty by design)
```

## What it is

The storefront product's first service, and **the platform's first anonymous read of tenant
data**. A buyer with no token, no account and no session asks whether a store delivers to their
postal code, and gets a real answer. Behind that one public route, a merchant configures the
delivery map that answers it.

This slice builds the `delivery` module and the product's identity row. The store document, the
buyer catalogue, the cart and the order engine are each their own ADR.

## Owns

| Table | What it is |
|---|---|
| `storefronts` | The product's identity row: `slug`, `status`, `currency`, `timezone`. One per organization in slice 1. `custom_domain` is a slice-2 placeholder. |
| `delivery_zones` | A named set of postal codes plus the behaviour for all of them — mode, promise, and the **display-only** fee and minimum order value. |
| `zone_postal_codes` | One row per postal code. `UNIQUE (organization_id, postal_code)`: one postal code, exactly one zone, no precedence rule. |
| `serviceability_demand` | A daily counter — `(org, date, postal code, outcome, checks)` — not a log. The public endpoint is an unauthenticated write, so a row per check would be unbounded. |

## Two roles, and why the second one exists

* `opsgrids_storefront` — NOBYPASSRLS, DML on this schema only. Everything a request does runs
  here, including an anonymous one.
* `opsgrids_storefront_directory` — **BYPASSRLS, SELECT-only on `storefront.storefronts`**.
  An anonymous request arrives with no organization, so the tenant has to be resolved from the
  store slug *before* a tenant-bound transaction can be opened. That lookup is
  `selectors.resolve_public_store`, and it is the entire surface of this role. Grants are applied
  in `migrations/env.py`, the `services/product` pattern verbatim.

**With the directory role unconfigured the service refuses to boot**, and that is load-bearing:
`get_directory_session` otherwise falls back to an ordinary session, the slug lookup matches
nothing under row-level security, and every buyer in the world is told the store does not deliver
to them — HTTP 200, no error, no log.

## Three rules a change here must not break

1. **One chokepoint.** Every serviceability answer — public, merchant, and eventually order
   placement — is produced by `selectors.resolve_serviceability`, inside a tenant-bound
   transaction. No caller computes it, caches it, or passes one in.
2. **Unknown is not no.** A check that fails is a non-2xx; the client renders "couldn't check".
   The server never returns an `UNSERVED` outcome it did not compute, and `app/api/public.py`
   contains no exception handling at all — asserted against its syntax tree in
   `tests/test_public_serviceability.py`.
3. **Variables never go in a public path.** `GatewayPolicy.public_paths` is an exact-path
   frozenset and `full_path` excludes the query string, so `/public/store/{slug}` is not
   expressible. Every anonymous endpoint carries its variables in the query string or the body.

## Publishes

Nothing. ADR-0010 §14 emits no event: a type with no consumer is a contract kept compatible
forever in exchange for nothing. The outbox table exists and stays empty until the order engine
needs something from it.

## Consumes

Nothing yet. The consumer that would flip a store's `status` to `suspended` when the
`storefront` entitlement lapses is named in ADR-0010 §4, is not built, and has no owner — until
it exists, a lapsed merchant keeps answering serviceability until somebody unpublishes them.
