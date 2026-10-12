# Unblock orders frozen on `invoice_timing = on_accept` (one-off repair)

**Status: prepared, NOT run. Needs the owner's explicit approval before it touches production.**

## Why

Until #137 (INVENTORY-02), every organization ran on the default `invoice_timing = 'on_accept'`.
An order freezes its policy into `orders.settings_snapshot` when it is accepted, and the handover
gate (`services/operations/app/orders/services.py`, `check_billing_gate`) reads the snapshot, not
the current settings. Orders accepted before #137 therefore still require `invoice_id` before
READY -> HANDED_OVER, and no invoice can be set while order invoicing is unwired (INVENTORY-01). #137
changes the default for orders accepted after it deploys; it does not touch existing orders.

## Affected records (read-only query, production, 2026-10-12)

Non-terminal orders (`ACCEPTED`, `IN_FULFILMENT`) with `settings_snapshot->>'invoice_timing'` in
(`on_accept`, `on_dispatch`) and `invoice_id IS NULL`: **6 orders in 5 organizations**.

| Organization (slug) | Status | Orders | Created |
|---|---|---|---|
| `f3-verify-870086e3` | ACCEPTED | 1 | 2026-09-28 |
| `f3-verify-eddba87e` | IN_FULFILMENT | 1 | 2026-09-28 |
| `f3-verify-20df1bd8` | IN_FULFILMENT | 1 | 2026-09-28 |
| `f3-verify-394de512` | ACCEPTED | 1 | 2026-09-28 |
| `opsgrids` | ACCEPTED | 2 | 2026-09-29 |

The four `f3-verify-*` organizations are throwaway verification tenants. Recommended: remove them with
`tools/purge_test_orgs.py` (owner-run, its own approval) rather than repair their orders. The repair
below is scoped to the `opsgrids` organization's two orders.

## Invariants the repair keeps

- Only `settings_snapshot.invoice_timing` changes, from `on_accept`/`on_dispatch` to `on_delivery`
  (the new default). No other snapshot key, status, payment or line changes.
- Terminal and draft orders are untouched; an order that already has an invoice is untouched.
- Every changed order gets one `operations.audit_logs` row (`action = 'order.settings_snapshot.repaired'`)
  carrying the old and new value, so the change is attributable and reversible.
- One transaction; it refuses to commit if the row count differs from the dry run.

## Run (owner-approved only)

Take a backup first: `make edge-backup HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com`.
Then, on the box, in `psql` against the production database:

```sql
BEGIN;

-- 1. Dry run: expect exactly 2 rows.
CREATE TEMP TABLE repair_targets ON COMMIT DROP AS
SELECT o.id, o.organization_id, o.settings_snapshot->>'invoice_timing' AS old_timing
  FROM operations.orders o
  JOIN account.organizations a ON a.id = o.organization_id
 WHERE a.slug = 'opsgrids'
   AND o.deleted_at IS NULL
   AND o.status IN ('ACCEPTED', 'IN_FULFILMENT')
   AND o.invoice_id IS NULL
   AND o.settings_snapshot->>'invoice_timing' IN ('on_accept', 'on_dispatch');

DO $$ BEGIN
  IF (SELECT count(*) FROM repair_targets) <> 2 THEN
    RAISE EXCEPTION 'expected 2 target orders, found %', (SELECT count(*) FROM repair_targets);
  END IF;
END $$;

-- 2. Audit rows first (old and new value per order).
INSERT INTO operations.audit_logs (id, organization_id, actor_id, acting_as, action, entity,
                                   entity_id, before, after, reason, at)
SELECT gen_random_uuid(), t.organization_id, NULL, 'repair:2026-10-12-unblock-on-accept-orders',
       'order.settings_snapshot.repaired', 'order', t.id,
       jsonb_build_object('invoice_timing', t.old_timing),
       jsonb_build_object('invoice_timing', 'on_delivery'),
       'INVENTORY-02: accepted before #137 under the dead-end on_accept default; invoicing unwired (INVENTORY-01)',
       now()
  FROM repair_targets t;

-- 3. The change.
UPDATE operations.orders o
   SET settings_snapshot = jsonb_set(o.settings_snapshot, '{invoice_timing}', '"on_delivery"')
  FROM repair_targets t
 WHERE o.id = t.id AND o.organization_id = t.organization_id;

-- 4. Verify, then COMMIT (or ROLLBACK if anything looks wrong).
SELECT o.id, o.status, o.settings_snapshot->>'invoice_timing'
  FROM operations.orders o JOIN repair_targets t ON t.id = o.id;

COMMIT;
```

Check the column list of `operations.audit_logs` against `libs/platform-core/src/platform_core/audit.py`
(`AuditLogMixin`) before running; adjust only if a NOT NULL column was added since.

## Undo

```sql
BEGIN;
UPDATE operations.orders o
   SET settings_snapshot = jsonb_set(o.settings_snapshot, '{invoice_timing}', to_jsonb(l.before->>'invoice_timing'))
  FROM operations.audit_logs l
 WHERE l.action = 'order.settings_snapshot.repaired'
   AND l.acting_as = 'repair:2026-10-12-unblock-on-accept-orders'
   AND l.entity_id = o.id AND l.organization_id = o.organization_id;
COMMIT;
```

## After

Open each repaired order in Operations and hand it over; it should no longer return 409. The
affected-records query above should then return only the `f3-verify-*` rows (or none once purged).
