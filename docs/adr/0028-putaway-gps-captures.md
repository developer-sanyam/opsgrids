# ADR-0028 — Storage put-away: where a worker stood is audit evidence, kept 30 days

**Status:** Accepted by the owner, 2026-10-05 — built on `feat/warehouse-map-putaway`, not deployed ·
**Date:** 2026-10-05 · **Service:** `operations` ·
**Design:** [warehouse map, find-stock and storage put-away](../superpowers/specs/2026-10-05-warehouse-map-putaway-design.md)

Builds on [ADR-0000](0000-phase-0-decisions.md) §9 (append-only is a table privilege), §10 (a schema per
service, no cross-schema foreign keys) and "Also settled" (PII: a distinct permission **and an audit row every
time**); [ADR-0002](0002-row-level-security.md) §2 (which role may do what); [ADR-0014](0014-inventory-and-manufacturing.md)
§12, amended by the same change (`putaway` joins the move reference vocabulary). It does not touch
[ADR-0025](0025-order-and-task-state.md), whose "putaway task" (the order-cancel reversal) is a different thing.
This ADR records decision **D3** of the design. The other three owner decisions of that day are one-liners
(below) and live in the design.

## Context

When a storekeeper puts received stock away, the worker scans the destination bin's barcode. The bin is the
location of record: it is what the ledger moves stock to and what every later screen reads. At the same moment
the worker's phone can report where it is. That fix is **proof the put-away happened where it was said to**, not
a second location: it never moves stock, never overrides the scanned bin, and a put-away with no fix (no
permission, no signal, an old phone) goes ahead unchanged.

A position tied to a named worker at a named time is personal location data. ADR-0000's PII rule names bank
accounts and government identifiers, and a coordinate is not on that list, so the platform would let it sit in
an ordinary column. That is the weakest reading, and the owner chose a stricter one.

## Decision (D3)

1. **A capture is its own append-only row.** `operations.putaway_captures`, one per put-away move (unique on
   `(organization_id, move_id)`): the scanned bin, the bin the server suggested, the worker (nullable: a manager
   with no worker row can still put stock away), the actor, the fix as integer micro-degrees (`lat_micro`,
   `lng_micro`, never a float) and `accuracy_m`, the device's claimed `captured_at`, and the server's
   `created_at`. `UPDATE` and `DELETE` are revoked from the app role. It is **not** stored on the move or the
   ledger: those rows are append-only for good reason (ADR-0000 §9, ADR-0014 §3), so a coordinate written there
   could never be erased.
2. **Plaintext at rest, protected by permission, audit and erasure rather than by envelope encryption.** The
   precedent for a column that ADR-0000 does not class as PII is a plain column. This decision keeps that and adds
   what binds the *reader*: a dedicated permission (3), an audit row on every read (3) and a deadline (4). That is
   stricter than precedent without a second key to manage. Encryption is rejected below.
   **Why not encrypt:** nothing searches or joins on a coordinate, and the platform's envelope derives its keys from
   one passphrase, so it protects a stolen database file or backup but not anyone holding the application's key.
3. **Coordinates leave the database through exactly one response.** `POST
   /operations/inventory/putaway/{move_id}/location:reveal` (POST only, so it is never cached, prefetched or
   logged as a URL; `Cache-Control: no-store`), behind the new permission
   `operations.inventory.putaway.location_read`: ORG scope, dangerous, four segments like the other reveal
   codes, held by the **ADMIN** role alone (`OPERATIONS_ADMIN` through `all()`, picked into no other template).
   **Every successful call writes one `entity.sensitive_read` audit row** (field names only, never a value),
   including when the capture has no fix; a 403 or 404 writes nothing. Everywhere else a coordinate does not
   appear: not in any other response (the put-away response and the detail read say only `has_location`), not in
   the audit `after` of any other row, not in an outbox payload (`operations.inventory.putaway.recorded` carries
   `has_location`), not in a log line (the service logs ids and counts). The only other place it travels is the
   inbound POST body.
4. **Kept 30 days, then erased by an admin-run procedure, not an in-app job.** The clock is the server's
   `created_at`, not the device's `captured_at`. The tool is `tools/purge_putaway_captures.py`: it connects as
   `opsgrids_admin` (the one role that still holds `DELETE`), is a **read-only dry run unless `--apply`**, takes
   `--organization-id`, prints **counts only**, and has no option to change the 30 days. It is not a job the
   service runs because the service cannot delete the rows by design, because erasure of a person's data is
   a decision someone takes and keeps the output of, and because a new always-on sweep is a cost on a memory-tight
   box for something that runs a few times a month. The RUNBOOK ("Warehouse map and storage put-away") carries the
   procedure and recommends weekly, so the real retention is 30 days plus the gap between runs.
5. **A purged capture is gone and says so.** The reveal of a purged capture, and the detail read of its
   put-away, answer 404 ("capture expired"); the move, the bin and every audit row are untouched.
6. **Bad geo never blocks the work.** Invalid or unpaired coordinates are dropped with a warning in the
   response, an implausible `captured_at` is replaced by the receipt time, and a failed capture insert leaves the
   stock move in place. The fix is not part of the idempotency request hash, so a retry carrying different geo
   replays the original put-away and records no second capture.

### The other three decisions of 2026-10-05, for the reader who lands here first

| # | Decision | Where it is carried |
|---|---|---|
| D1 | Storage put-away is allowed only **from `receiving` or `staging` bins**. The order engine reserves only at `item.home_location_id`, so stock in a rack bin is invisible to order accept until multi-location allocation exists: documented as a current limitation. | The design §2 and §12; RUNBOOK |
| D2 | **No Content-Security-Policy change** on `ops`/`worker`; CSP hardening is a separate release. The Google Cloud project, billing, spend cap and the key in the box `.env` are the owner's. The map is fully disabled, never broken, when `VITE_GOOGLE_MAPS_API_KEY` is absent. | `publish-web.sh`, `.env.example`, RUNBOOK |
| D4 | The name **"storage put-away"** is approved; ADR-0014 §12's move reference vocabulary gains `putaway`; ADR-0025's "putaway task" is left alone. | ADR-0014 §12, amended |

## What this rejects

- **Encrypting the fix with `platform_core.pii`.** See (2). It would add an unseal step to the one read path and a
  key-rotation concern to a table that lives 30 days. Revisit it if the database becomes readable to more people
  than it is today.
- **Storing the fix on the ledger or move row.** Immutable rows cannot honour an erasure.
- **An in-app retention sweep.** See (4). Also the sweep's own role would need `DELETE` on an append-only table,
  which is exactly the privilege the app role is denied.
- **Nulling the coordinates and keeping the row.** Simpler to audit that a put-away had a capture, but the
  owner chose to delete rows. It is a one-line change in the tool if that preference ever flips.
- **A `GET` for the reveal.** A URL that returns a person's position gets cached, prefetched and logged.

## Consequences and what is left open

- **Plaintext, append-only storage means erasure is an operator's act.** Between a capture and its purge, anyone
  who can read the database, a backup or a restore can read the coordinates. The 30 days bound that; the backups
  taken before a purge keep the rows until they age out
  ([engage-erasure-and-unsubscribe.md](../engage-erasure-and-unsubscribe.md) says the same of every delete here,
  with the WAL and dead-tuple caveats).
- **The purge writes no audit row of its own.** Its printed counts, kept with the change record, are the
  evidence. If an auditor wants a tamper-evident erasure log, add a per-organization audit row to the tool; the
  deletion statement does not change.
- **The tool purges by age and organization only.** An erasure of one worker's captures is a hand-run `DELETE` as
  `opsgrids_admin` after a backup, and an owner decision.
- **The reveal is the only reader.** Anything that wants coordinates in bulk (a heat map of where stock is put
  away, say) is a new decision, not an extension of this one.
- **ADMIN-only describes the system role templates.** No template but the administrator's includes the
  permission. It is not a promise about a role an organization authors for itself; revisit that if it matters.
