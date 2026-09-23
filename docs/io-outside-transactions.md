# I/O outside database transactions (D3)

**Rule.** No network call — a government portal, a payment provider, DNS, object storage, another
service's HTTP API — runs while a database transaction is open on the caller's connection.

**Why it is a rule and not a style.** An open transaction holds a pool connection, and usually a
row lock, for as long as the remote end takes to answer. The pools here are small (3 + 2 per
process); a handful of requests stuck on a slow portal exhausts one, and every unrelated request
on that replica queues behind them. And a process that dies mid-call inside a transaction rolls
back everything it meant to record, so the platform loses the evidence that it asked at all —
while the remote side may have acted.

**The shape every fix takes: claim / call / record.**

1. **Claim** — one short transaction. Refuse everything answerable locally (404, 409, 422);
   write what the call needs to be resumed from, if anything must be written (see "claim rows"
   below); commit.
2. **Call** — no session, no transaction. The provider's answer is folded into a plain outcome
   value (`filed` / `generated` / `cancelled`, `rejected`, `unavailable`).
3. **Record** — a second short transaction. Append the row the outcome decided, audit, stage
   events on the outbox. Idempotent against a second recorder of the same claim.

A crash between (2) and (3) is recovered by a sweep that finds the claim (or the absence of any
row) past a deadline and runs the same three acts again; the provider's own idempotency is what
makes the repeat call safe.

**The guard.** `tests/infra/test_io_outside_transaction.py` scans every service for a call that
reaches a provider seam (`IrpClient`, `EwayClient`, `ImsClient`, `PaymentProvider`,
`ProductRegistry`, `ObjectStore`, `Resolver`, `ProbeSet`, `HierarchySource`, `IdpClient`) from
inside `session.begin()`, `session_scope(...)`, a sweep's `_work_scope(...)`, or a route wrapped
end to end by a committing dependency (`get_tenant_session` and its siblings). Anything it finds
must be fixed or sit on its `ALLOWLIST` with the fix it needs; an allow-list entry that no longer
reproduces fails the suite, so a fixed instance cannot linger there.

## The instance table

Status: **fixed** — split, with a test proving no transaction is open during the call;
**allow-listed** — known, on the guard's allow-list with the fix written beside it;
**routed** — belongs to another slice's owner, reported there, not changed here.

Rows #1–#5 are round 1 of this slice; the catalogue this table was cut from does not number
them in-repo, so they are numbered here in the order they landed.

| # | Service | Where | Remote call | Status | Fix, and the test that proves it |
|---|---|---|---|---|---|
| 1 | finance | `POST /internal/v1/einvoice/filings` (`api/internal.py:file_invoice`) | `IrpClient.register` | fixed (round 1) | `claim_manual_filing` → `call_irp_register` → `record_manual_filing`. `test_filing_transaction_boundary.py::TestNoTransactionDuringTheIrpCall::test_the_internal_route_holds_no_transaction_while_the_portal_is_called` |
| 2 | finance | consumer `file_requested_invoice` | `IrpClient.register` | fixed (round 1) | `claim_autofile_attempt` → call → `record_autofile_result`, each in its own `session_scope`. `…::test_the_consumer_holds_no_transaction_while_the_portal_is_called`; crash recovery `TestCrashBetweenCallAndRecord` |
| 3 | payment | `POST /internal/v1/charges` (`api/internal.py:create_charge`) | `PaymentProvider.charge` | fixed (round 1) | `stage_charge` → `call_provider_charge` → `finalize_charge`; stuck-charge sweep in `app/cron.py`. `payment/tests/test_charge_transaction_boundary.py` |
| 4 | payment | consumers `charge_on_invoice_finalized`, `charge_on_dunning_retry` | `PaymentProvider.charge` | fixed (round 1) | same split. `…::test_the_consumer_holds_no_transaction_while_the_provider_is_called` |
| 5 | notification | Engage `verify_domain` | DNS `Resolver` | fixed (round 1) | read, resolve with no session, record. `notification/tests/test_domain_verify_transaction_boundary.py` |
| 6 | finance | `POST /internal/v1/einvoice/filings:cancel` (`cancel_filing`) | `IrpClient.cancel` | **fixed (round 2)** | `claim_irn_cancellation` (a `cancelling` row: reason, actor, audited `einvoice.cancel_requested`) → `call_irp_cancel` → `record_irn_cancellation`; the sweep resumes `cancelling`; "already cancelled" (`IrpAlreadyCancelled`) is success-by-fetch. `test_compliance_transaction_boundary.py::TestIrnCancellation` — `test_the_route_holds_no_transaction_while_the_portal_is_called`, `test_a_crash_after_the_portal_withdrew_it_is_recorded_as_cancelled`, `test_an_outage_answers_with_the_claim_and_the_sweep_finishes_it`, `test_a_claim_nobody_can_finish_becomes_a_visible_failure`, `test_two_recorders_of_one_claim_write_one_withdrawal`, `test_a_claim_a_request_is_holding_is_skipped_not_waited_on` |
| 7 | finance | `POST /internal/v1/eway/bills` (`generate_eway_bill`) | `EwayClient.generate` | **fixed (round 2)** | `claim_eway_generation` (a `submitting` row carrying the transport facts) → `call_eway_generate` under the (document, transporter/vehicle part) key → `record_eway_generation`; the sweep resumes `submitting`/`pending`; "already generated" (`EwayAlreadyGenerated`) is success-by-lookup. `TestEwayGeneration` — `test_the_route_holds_no_transaction_while_the_portal_is_called`, `test_a_crash_after_the_portal_issued_the_bill_records_that_bill`, `test_an_outage_is_pending_and_the_sweep_raises_it_later`, `test_a_generation_in_flight_refuses_a_second`, `test_a_generation_nobody_can_finish_becomes_a_visible_failure`; key: `TestTheEwayIdempotencyKey` |
| 8 | finance | `POST /internal/v1/eway/bills:cancel` (`cancel_eway_bill`) | `EwayClient.cancel` | **fixed (round 2)** | `claim_eway_cancellation` (a `cancelling` row) → `call_eway_cancel` → `record_eway_cancellation`; the sweep resumes `cancelling`; `EwayAlreadyCancelled` is success-by-fetch. `TestEwayCancellation` — `test_the_route_holds_no_transaction_while_the_portal_is_called`, `test_a_crash_after_the_portal_withdrew_it_is_recorded_as_cancelled`, `test_an_outage_answers_with_the_claim_and_the_sweep_finishes_it` |
| 9 | finance | `POST /internal/v1/ims/sync` (`sync_ims`) | `ImsClient.fetch` | **fixed (round 2)** | `claim_ims_sync` (read-only) → `fetch_ims_period` → `record_ims_fetch` + `reconcile_ims_documents` in one short transaction; ingest is `INSERT … ON CONFLICT (organization_id, source_ref) DO NOTHING RETURNING`. `TestImsSync` — `test_the_route_holds_no_transaction_while_the_portal_is_fetched`, `test_the_ingest_is_idempotent_on_the_portals_record_key`, `test_two_syncs_racing_one_period_both_succeed`, `test_an_outage_is_a_503_and_leaves_nothing_behind` |
| 10 | catalog | `api/admin.py:create_version` | `ProductRegistry.products` (HTTP to product-service) inside `get_operator_session` | allow-listed | resolve the registry in its own dependency before the session opens; pass the fetched product map into `create_version` instead of the client |
| 11 | catalog | `bundle_audit.py:main` (operator CLI) | `ProductRegistry.products` inside `session.begin()` | allow-listed | fetch the registry first, open the transaction after (low severity: nothing contends for that connection) |
| 12 | document | `api/routes.py:upload_asset` | `ObjectStore.put` (S3) inside `get_tenant_session` | allow-listed | quota read first; PUT with no session; then open the transaction to insert the asset and audit |
| 13 | document | `api/routes.py:delete_asset` (`purge=true`) | `ObjectStore.delete` | allow-listed | delete the object with no session; then mark the row purged and audit |
| 14 | document | `cron.py:_purge` (retention sweep) | `ObjectStore.delete` inside `session_scope` | allow-listed | same shape as #13, per asset |
| 15 | storefront | `api/routes.py:check_domain` | DNS/TLS `ProbeSet` | allow-listed | lock and re-verify the claim, release, probe with no transaction, re-lock to record |
| 16 | storefront | `cron.py:_advance_one` | DNS/TLS `ProbeSet` inside `session_scope` | allow-listed | same shape as #15 |
| 17 | subscription | `create_subscription`, `change_plan`, `subscribe_as_operator`, `change_plan_as_operator` | plan-version pin lookup against catalog over HTTP | routed (subscription slice), allow-listed | resolve the pin before the transaction opens, then write the subscription/change |
| 18 | auth | `api/routes.py:save_sso_config` | OIDC discovery via `IdpClient` | routed (auth slice), allow-listed | run discovery and validate endpoints untransacted; open the transaction only to seal the secret, write, audit and stage the event |
| 21 | payment | `services.create_refund` | `PaymentProvider.refund` | routed (payment owner) | not flagged by the guard today because nothing calls it — there is no refund endpoint yet. The day one is added it must be split like `create_charge` (#3): stage a `refunds` row keyed `refund:{refund_id}` (already the provider idempotency key) in a short transaction, call with none open, record the provider's answer, and extend the stuck-charge sweep to resume a staged refund |

## The filing contract (finance, round 2)

Three government portals, two ledgers (`einvoice_filings`, `eway_bills`), both append-only; the
newest row for an invoice is its current position. Newest means `attempted_at`, then
`created_at`, then **an outcome ahead of a claim at an exact tie** (`selectors._newest_first`) —
a claim and its outcome share `attempted_at` by design, and in one transaction share `created_at`
too.

### States

| Ledger | Status | Kind | Written by | Meaning |
|---|---|---|---|---|
| e-invoice | *(no row)* | — | — | registration claims write nothing: the IRN is a pure function of the document, so a repeat call is safe and `IrpDuplicate` is the portal saying so |
| e-invoice | `pending` | outcome | record (outage) | the portal did not answer; a retry is owed (scheduled by the consumer, or — for a manual attempt — owed to the sweep) |
| e-invoice | `filed` / `failed` | outcome | record | registered / refused (or `retry_exhausted`) |
| e-invoice | `cancelling` | **claim** | `claim_irn_cancellation`, sweep lease | a withdrawal was asked for: reason code in `error_code`, remark in `error`, the acknowledgement it withdraws in `ack_no`/`ack_date`; actor in `created_by` and the `einvoice.cancel_requested` audit row. No IRN on the row (the `(org, irn)` unique index) |
| e-invoice | `cancelled` | outcome | record | withdrawn |
| e-Way | `submitting` | **claim** | `claim_eway_generation`, sweep lease | a generation was asked for; the row carries the transport facts, which are on no invoice |
| e-Way | `pending` | outcome | record (outage) | the portal did not answer; owed to the sweep |
| e-Way | `generated` / `failed` | outcome | record | a bill number and its computed validity / a refusal (or `retry_exhausted`) |
| e-Way | `cancelling` | **claim** | `claim_eway_cancellation`, sweep lease | a withdrawal was asked for; no bill number on the row (the `(org, eway_bill_no)` unique index) — the bill is the `generated` row behind it (`selectors.generation_under_claim`) |
| e-Way | `cancelled` | outcome | record | withdrawn |

A new request is refused with **409** while the newest row is a claim (`cancelling`, `submitting`)
— the first request's intent, with its reason or its transport facts, is what gets finished.

**Read model.** Claim rows stay on the ledger (it is append-only), but a history
(`GET /finance/invoices/{id}/filings`, `…/eway-bills`) and the e-Way list show a claim only while
it is its document's newest row — an attempt in flight — never once answered
(`selectors.visible_attempts`, and the same rule in SQL for the paged list). `attempts` on
`EInvoiceState` / `EwayState` counts outcome rows only. So a resolved withdrawal still reads
`cancelled, filed`; a withdrawal the portal has not yet confirmed reads `cancelling, filed`, with
`status: "cancelling"`, `terminal: false`, `remedy: null` on the invoice's e-invoice state.

### What the route returns

* cancel IRN / cancel e-Way: **201** with the newest row — `cancelled`, `failed` (portal refusal),
  or the `cancelling` claim itself when the portal was unreachable (the withdrawal is owed and the
  sweep finishes it).
* generate e-Way: **201** with `generated`, `failed`, or `pending` (outage).
* IMS sync: **200**; a portal outage is **503** and writes nothing.

### Who resumes what (`app/cron.py`, `FilingReclaimSweep`, one process, `bin/cron`)

| Found (newest row, cross-tenant discovery) | Deadline, re-checked under the tenant binding | Action |
|---|---|---|
| reportable invoice with **no filing row** (`due_filing_reclaims`) | invoice older than `FILING_RECLAIM_AFTER` (5 min) and no `filing_requested` staged since its creation within the last 5 min | re-publish `filing_requested` (attempt 1); the consumer claims, calls, records |
| e-invoice `pending` (`due_stale_filings`) | `pending_redrive_after(n)` = the retry's scheduled delay + 5 min grace, and no `filing_requested` staged since the row within the last 5 min — "no live scheduler job", inferred because the scheduler's table is not finance's to read | re-publish `filing_requested` at attempt `n + 1` (capped at `MAX_FILING_ATTEMPTS`); the consumer's outage on the last attempt is terminal |
| e-invoice `cancelling` | `resume_after(n)` | append a fresh `cancelling` (the lease), call `IrpClient.cancel` with no transaction, record |
| e-Way `submitting` or `pending` | `resume_after(n)`, `n` = `submitting` rows in the leading chain | append a fresh `submitting` (the lease), call `EwayClient.generate` under the same key, record |
| e-Way `cancelling` | `resume_after(n)` | append a fresh `cancelling`, call `EwayClient.cancel`, record |

* **Bounded backoff.** `resume_after(n)` = 5 min for the first re-drive, then the filing retry's
  own capped exponential (7, 11, 19, 35 min … capped at 63).
* **Lease.** Each re-drive appends a fresh claim row, so "newest row older than its deadline" is
  itself the lease; the invoice is taken `FOR UPDATE SKIP LOCKED`, so one a request or another
  replica holds is skipped this tick rather than waited on. Claims and records lock it plainly.
* **Poisoned-step guard.** After `MAX_COMPLIANCE_RESUMES` (5) drives a claim becomes a terminal
  `failed` row carrying `retry_exhausted` (plus `eway.failed` / `EVT_EWAY_FAILED retryable:false`
  for a generation), visible on the read model as `terminal: true`. An exception on one invoice is
  logged with its identifiers and counted; the tick continues.
* **Discovery role.** The three discovery queries run on `opsgrids_outbox` (BYPASSRLS), which
  holds **column-scoped** `SELECT` on exactly `invoices(organization_id, id, supply_type,
  document_type, status, created_at, deleted_at)`, `einvoice_filings(organization_id, id,
  invoice_id, status, attempted_at, created_at, deleted_at)` and `eway_bills(` the same seven `)`
  — granted after every alembic run by `services/finance/migrations/env.py:grant_sweep_reader`,
  `REVOKE ALL` first. Every write runs on the app role under `session_scope(organization_id=…)`.

### Provider idempotency

| Call | Idempotent by | A repeat is answered with | Recorded as |
|---|---|---|---|
| `IrpClient.register` | the document (IRN = hash of GSTIN, type, number, FY) | `IrpDuplicate(ack)` | `filed` |
| `IrpClient.cancel` | the IRN | `IrpAlreadyCancelled(ack)` | `cancelled` |
| `EwayClient.generate` | `EwayPayload.idempotency_key` = `domain.eway_idempotency_key`: invoice, Part B (mode, vehicle, transporter, transport document) and the generation ordinal (count of earlier `generated` rows + 1) | `EwayAlreadyGenerated(ack)` | `generated` with the portal's original number |
| `EwayClient.cancel` | the bill number | `EwayAlreadyCancelled(ack)` | `cancelled` |
| `ImsClient.fetch` | read-only | — | ingest is idempotent on `(organization_id, source_ref)` at the database |

A real adapter must map the portal's "already done" error codes onto these exceptions rather than
onto `…Rejected`; that mapping is what turns a crash-then-resume into a success instead of a
spurious failure. Each `record_*` is also idempotent on our side: the invoice is row-locked, the
newest row re-read, and an outcome that somebody else already recorded (the same bill number, a
`cancelled` row, a claim already resolved) appends nothing.
