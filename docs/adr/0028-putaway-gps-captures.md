# ADR-0028 — Storage put-away: where a worker stood is audit evidence, kept 30 days

**Status:** Accepted by the owner, 2026-10-05 — built on `feat/warehouse-map-putaway`, not deployed. One part of
(3), the one-time-code requirement on the reveal, was added in review beyond what the owner approved and is **an
owner decision still open: keep it, or revert** (see "Owner decision" below) ·
**Date:** 2026-10-05 · **Service:** `operations` ·
**Design:** [warehouse map, find-stock and storage put-away](../superpowers/specs/2026-10-05-warehouse-map-putaway-design.md)

Builds on [ADR-0000](0000-phase-0-decisions.md) §9 (its related immutability rules: ledgers and usage events are
append-only), §10 (every table carries `organization_id` under row-level security; no cross-schema foreign keys) and
"Also settled" (PII: a distinct permission **and an audit row every time**);
[ADR-0002](0002-row-level-security.md) (§2, which role may do what; its migration sequence, step 0, which makes
append-only a table privilege withheld from the app role rather than a policy); [ADR-0014](0014-inventory-and-manufacturing.md)
§3 (the ledger and its moves are append-only for the same reason) and §12, amended by the same change (`putaway` joins
the move reference vocabulary); and [ADR-0023](0023-member-step-up.md) (the one-time-code requirement on reveal
routes). It does not touch [ADR-0025](0025-order-and-task-state.md), whose "putaway task" (the order-cancel reversal)
is a different thing.
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
   ledger: those rows are append-only for good reason (ADR-0014 §3; ADR-0000 §9 sets the same rule for the platform's
   other ledgers), so a coordinate written there could never be erased.
2. **Plaintext at rest, protected by permission, audit and erasure rather than by envelope encryption.** The
   precedent for a column that ADR-0000 does not class as PII is a plain column. This decision keeps that and adds
   what binds the *reader*: a dedicated permission (3), an audit row on every read (3) and a deadline (4). That is
   stricter than precedent without a second key to manage. Encryption is rejected below.
   **Why not encrypt:** nothing searches or joins on a coordinate, and the platform's envelope derives its keys from
   one passphrase, so it protects a stolen database file or backup but not anyone holding the application's key.
3. **Coordinates leave the database through exactly one response, and that response needs a one-time code.**
   `POST /operations/inventory/putaway/{move_id}/location:reveal` (POST only, so it is never cached, prefetched or
   logged as a URL; `Cache-Control: no-store`), behind the new permission
   `operations.inventory.putaway.location_read`: ORG scope, dangerous, four segments like the other reveal
   codes, held by the **ADMIN** role alone (`OPERATIONS_ADMIN` through `all()`, picked into no other template).
   **The route also demands a session that gave a one-time code** (`amr={"otp"}`, the pairing every reveal route
   in the platform uses, ADR-0023): the permission says the role may, `otp` says this token went through a second
   factor, so a stolen or unattended administrator token is not a standing route to every worker's position for
   30 days (the audit row records a bulk pull; it does not stop one). A token without `otp` is answered 403
   (`Additional authentication required: otp`) before anything is looked up, so it also confirms nothing about
   which moves hold a fix, and it writes nothing. **This requirement was added in review, beyond the owner's
   literal D3 text** ("only ADMIN may reveal"); see "Owner decision" below.
   **Every successful call writes one `entity.sensitive_read` audit row** (field names only, never a value),
   including when the capture has no fix; a 403 or a 404 writes nothing. The service puts a coordinate in no other
   place: not in any other response (the put-away response says only `geo_recorded`, and the detail read and the
   `operations.inventory.putaway.recorded` event say only `has_location`), not in the audit `after` of any other
   row, not in an outbox payload, not in a log line (the service logs ids and counts).
   **The reveal is not the only other place a coordinate goes, and this ADR does not claim it is.** Besides the
   reveal's own response there are three:
   - **The inbound POST body** that carries the fix from the worker's phone. Until the put-away syncs, the fix
     also sits in the worker app's offline queue in the phone's own storage (dropped on success and stripped from
     an entry that is parked).
   - **The administrator's browser**, which holds the revealed fix in memory until the drawer is closed.
   - **Google Maps, when the reveal draws its map.** With `VITE_GOOGLE_MAPS_API_KEY` in the operations bundle, the
     reveal shows the position on a Google map, and the browser asks Google for the tiles around it. Google
     therefore learns the **viewport position** (street level for a good fix, since the map opens at zoom 18), together with what any
     request to Google carries: the key, the page's origin and the administrator's IP address. The app sends it no
     worker id, move id or organization, and the pin and accuracy circle are drawn in the browser; but a position
     is disclosed to a third party, and that is a consequence of choosing a Google map, not something this ADR can
     contain. **A text-only reveal exists when the map is disabled:** with no key in the bundle (the owner's
     choice, D2) or when Maps fails to load, the reveal shows the coordinates as text and nothing is sent to
     Google. There is no per-click choice today: a build with a key always draws the map.
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

## Owner decision: keep the one-time code, or revert to permission and audit only

The one-time-code requirement in (3) is **not** in the owner's decision of 2026-10-05, which was the permission plus
an audit row on every call. It was added during review as a security hardening and is **kept in the build until the
owner decides.** The choice:

- **Keep it.** A stolen or unattended administrator token cannot pull workers' positions without a second factor.
  The cost is that an administrator who has not enrolled an authenticator, whose session opened before they
  did, or who signed in through single sign-on or a social login (those sessions carry `sso` / `oauth`, never
  `otp`), gets a 403 on "Show worker location" and cannot reveal until they sign in with a code. The drawer
  reports it as an error; nothing is revealed and nothing is audited. The RUNBOOK says what to tell them.
- **Revert it.** One line: delete `amr={"otp"}` from `PutawayLocationRead` in
  `services/operations/app/inventory/api.py`, and flip the assertions in
  `services/operations/tests/test_putaway_authz.py::TestTheRevealNeedsASessionThatGaveAOneTimeCode`. The sentences
  here (3), in the design's §4.5, in the RUNBOOK and in the catalogue and service comments that mention the code are
  edited with it. The permission and the audit row remain, which is the literal D3.

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
- **Requiring the `{otp, stepup}` pair, as the CRM reveal routes do.** `stepup` is the marker only a fresh
  `POST /auth/mfa/step-up` mints, so only a UI that runs that flow can ever present it. **The operations SPA has no
  step-up UI**, so the pair would make "Show worker location" unusable for every administrator. `otp` alone is
  usable by any administrator whose session gave a code at sign-in, at the price ADR-0023 risk 5 records: `otp` is
  present for up to seven days of a session, so it shows the session went through a second factor, not that a code
  was given *for this reveal*. **Follow-up:** add a step-up prompt to the operations SPA (`useStepUp`, as the CRM
  app does) and then add the marker to this route's guard. `tests/infra/test_step_up_marker.py` pins where the
  marker's literal and name may appear, so that change edits the test with it.

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
- **The reveal is the only reader of the record.** Anything that wants coordinates in bulk (a heat map of where
  stock is put away, say) is a new decision, not an extension of this one. Drawing a revealed position on a Google
  map discloses its viewport to Google (see (3)); an owner who does not want that leaves the Maps key out of the
  operations bundle (D2) and the reveal is text only.
- **A capture that fails to write is not a failed request.** Bad geo never blocks the work (6), and neither does a
  database error writing the capture row: the move stands, the response is a 201 with the warning
  `geo_not_recorded`, and the proof is simply missing. Nothing in the error rate, and so no rollback trigger on 5xx,
  would catch it. The service logs `putaway.capture_failed` (move id and error class, never a value); the RUNBOOK
  tells the operator to count that line after a deploy and to alert on it.
- **ADMIN-only describes the system role templates.** No template but the administrator's includes the
  permission. It is not a promise about a role an organization authors for itself; revisit that if it matters.
