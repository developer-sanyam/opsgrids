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
2. **Call** — no session, no transaction, and **off the event loop**: every provider client here
   is synchronous, so the call runs on a worker thread bounded by a timeout
   (`finance.services.off_loop`, `payment.services.call_provider_charge_off_loop`). The answer is
   folded into a plain outcome value (`filed` / `generated` / `cancelled`, `rejected`,
   `unavailable`); a call that outlives its timeout is an `unavailable` (the thread is abandoned,
   not cancelled — the claim stands and the resume finds whatever it achieved).
3. **Record** — a second short transaction. Append the row the outcome decided, audit, stage
   events on the outbox. Idempotent against a second recorder of the same claim.

A crash between (2) and (3) is recovered by a sweep that finds the claim (or the absence of any
row) past a deadline and runs the same three acts again; the provider's own idempotency — or,
where the provider has none (Razorpay), **lookup before create** — is what makes the repeat call
safe. Consumers mark their message processed in the *claim* transaction, so the queue never
retries a crashed claim: **the sweep is the only recovery, and it runs inside the service's
existing worker process** (`finance-worker`, `payment-worker`, supervised — `app/worker.py`), not
a container of its own. `tests/infra/test_sweeps_run_somewhere.py` fails if any service's sweep
runs nowhere on the edge.

**The guard.** `tests/infra/test_io_outside_transaction.py` scans every service for a call that
reaches a provider seam (`IrpClient`, `EwayClient`, `ImsClient`, `PaymentProvider`,
`ProductRegistry`, `ObjectStore`, `Resolver`, `ProbeSet`, `HierarchySource`, `IdpClient`,
`Transport`, any `httpx.AsyncClient`) from inside `session.begin()`, `session_scope(...)`, a
sweep's `_work_scope(...)`, an autobegun `async with factory() as session:` block after its first
query, or a route wrapped end to end by a committing dependency (`get_tenant_session` and its
siblings, matched by exact name). Since round 3 it follows import aliases, function references
handed to `asyncio.to_thread` and friends, and locals bound to a seam or to a seam factory
(`get_*_client()`, `get_provider()`, `_default_resolver()`), and it flags a seam called inline
in the transaction block itself (`client.register(...)`), not only one behind a helper. Anything it finds must be fixed or sit on its `ALLOWLIST` with the fix it
needs; an allow-list entry that no longer reproduces fails the suite, so a fixed instance cannot
linger there.

## The instance table

Status: **fixed** — split, with a test proving no transaction is open during the call;
**allow-listed** — known, on the guard's allow-list with the fix written beside it;
**routed** — belongs to another slice's owner, reported there, not changed here.

Rows #1–#5 are round 1 of this slice; the catalogue this table was cut from does not number
them in-repo, so they are numbered here in the order they landed. #19, #20 and #22–#24 were found
by the round-3 scanner (new seams, seam locals, aliases); all are in already-merged code owned by
other slices, and are routed rather than changed here.

| # | Service | Where | Remote call | Status | Fix, and the test that proves it |
|---|---|---|---|---|---|
| 1 | finance | `POST /internal/v1/einvoice/filings` (`api/internal.py:file_invoice`) | `IrpClient.register` | fixed (round 1) | `claim_manual_filing` → `call_irp_register` → `record_manual_filing`. `test_filing_transaction_boundary.py::TestNoTransactionDuringTheIrpCall::test_the_internal_route_holds_no_transaction_while_the_portal_is_called` |
| 2 | finance | consumer `file_requested_invoice` | `IrpClient.register` | fixed (round 1) | `claim_autofile_attempt` → call → `record_autofile_result`, each in its own `session_scope`. `…::test_the_consumer_holds_no_transaction_while_the_portal_is_called`; crash recovery `TestCrashBetweenCallAndRecord` |
| 3 | payment | `POST /internal/v1/charges` (`api/internal.py:create_charge`) | `PaymentProvider.charge` | fixed (round 1; hardened round 3) | `stage_charge` (takes the call **lease**) → `call_provider_charge` (**lookup before create**: Order by receipt, payment by Order, then create; on a worker thread, bounded) → `finalize_charge` (charge `FOR UPDATE`; provider id set only if NULL; state moves only from `created`, never to a terminal state — a webhook's `captured` is never regressed); stuck-charge sweep in `payment-worker`, bounded by `charge_max_attempts` then `failed` + `commerce.payment.failed`. `payment/tests/test_charge_transaction_boundary.py`; round 3: `test_charge_lease_and_recovery.py` — `TestAWebhookIsNeverRegressed`, `TestOneCallAtATime`, `TestLookupBeforeCreate`, `TestTheSweepIsBounded`, `TestDiscoveryIsColumnScoped`; worker: `test_worker.py` |
| 4 | payment | consumers `charge_on_invoice_finalized`, `charge_on_dunning_retry` | `PaymentProvider.charge` | fixed (round 1) | same split. `…::test_the_consumer_holds_no_transaction_while_the_provider_is_called` |
| 5 | notification | Engage `verify_domain` | DNS `Resolver` | fixed (round 1) | read, resolve with no session, record. `notification/tests/test_domain_verify_transaction_boundary.py` |
| 6 | finance | `POST /internal/v1/einvoice/filings:cancel` (`cancel_filing`) | `IrpClient.cancel` | **fixed (round 2)** | `claim_irn_cancellation` (a `cancelling` row: reason, actor, audited `einvoice.cancel_requested`) → `call_irp_cancel` → `record_irn_cancellation`; the sweep resumes `cancelling`; "already cancelled" (`IrpAlreadyCancelled`) is success-by-fetch. `test_compliance_transaction_boundary.py::TestIrnCancellation` — `test_the_route_holds_no_transaction_while_the_portal_is_called`, `test_a_crash_after_the_portal_withdrew_it_is_recorded_as_cancelled`, `test_an_outage_answers_with_the_claim_and_the_sweep_finishes_it`, `test_a_claim_nobody_can_finish_becomes_a_visible_failure`, `test_two_recorders_of_one_claim_write_one_withdrawal`, `test_a_claim_a_request_is_holding_is_skipped_not_waited_on` |
| 7 | finance | `POST /internal/v1/eway/bills` (`generate_eway_bill`) | `EwayClient.generate` | **fixed (round 2)** | `claim_eway_generation` (a `submitting` row carrying the transport facts) → `call_eway_generate` under the (document, transporter/vehicle part) key → `record_eway_generation`; the sweep resumes `submitting`/`pending`; "already generated" (`EwayAlreadyGenerated`) is success-by-lookup. `TestEwayGeneration` — `test_the_route_holds_no_transaction_while_the_portal_is_called`, `test_a_crash_after_the_portal_issued_the_bill_records_that_bill`, `test_an_outage_is_pending_and_the_sweep_raises_it_later`, `test_a_generation_in_flight_refuses_a_second`, `test_a_generation_nobody_can_finish_becomes_a_visible_failure`; key: `TestTheEwayIdempotencyKey` |
| 8 | finance | `POST /internal/v1/eway/bills:cancel` (`cancel_eway_bill`) | `EwayClient.cancel` | **fixed (round 2)** | `claim_eway_cancellation` (a `cancelling` row) → `call_eway_cancel` → `record_eway_cancellation`; the sweep resumes `cancelling`; `EwayAlreadyCancelled` is success-by-fetch. `TestEwayCancellation` — `test_the_route_holds_no_transaction_while_the_portal_is_called`, `test_a_crash_after_the_portal_withdrew_it_is_recorded_as_cancelled`, `test_an_outage_answers_with_the_claim_and_the_sweep_finishes_it` |
| 9 | finance | `POST /internal/v1/ims/sync` (`sync_ims`) | `ImsClient.fetch` | **fixed (round 2)** | `claim_ims_sync` (read-only) → `fetch_ims_period` → `record_ims_fetch` + `reconcile_ims_documents` in one short transaction; ingest is `INSERT … ON CONFLICT (organization_id, source_ref) DO NOTHING RETURNING`. `TestImsSync` — `test_the_route_holds_no_transaction_while_the_portal_is_fetched`, `test_the_ingest_is_idempotent_on_the_portals_record_key`, `test_two_syncs_racing_one_period_both_succeed`, `test_an_outage_is_a_503_and_leaves_nothing_behind` |
| 10 | catalog | `api/admin.py:create_version` (and, since 2026-10-06, `create_plan` and `publish_version`) | `ProductRegistry.products` (HTTP to product-service) inside `get_operator_session` | **fixed (2026-10-06, ADR-0030)** | `get_product_snapshot` reads the registry in its own dependency, declared ahead of the session dependency, and hands the services a `ProductSnapshot` instead of the client; a failed read is kept and raised (503) only by a check that needs it (a plan's product key, a bundle's members). `catalog/tests/test_admin_plans.py::TestTheRegistryIsReadOutsideTheTransaction`; console's `ensure_bespoke_price` reads it the same way (its own `get_product_snapshot`, ahead of its session dependency; `test_console_internal.py`), because its publish needs it |
| 11 | catalog | `bundle_audit.py:main` (operator CLI) | `ProductRegistry.products` inside `session.begin()` | allow-listed | fetch the registry first, open the transaction after (low severity: nothing contends for that connection) |
| 12 | document | `api/routes.py:upload_asset` | `ObjectStore.put` (S3) inside `get_tenant_session` | allow-listed | quota read first; PUT with no session; then open the transaction to insert the asset and audit |
| 13 | document | `api/routes.py:delete_asset` (`purge=true`) | `ObjectStore.delete` | allow-listed | delete the object with no session; then mark the row purged and audit |
| 14 | document | `cron.py:_purge` (retention sweep) | `ObjectStore.delete` inside `session_scope` | allow-listed | same shape as #13, per asset |
| 15 | storefront | `api/routes.py:check_domain` | DNS/TLS `ProbeSet` | allow-listed | lock and re-verify the claim, release, probe with no transaction, re-lock to record |
| 16 | storefront | `cron.py:_advance_one` | DNS/TLS `ProbeSet` inside `session_scope` | allow-listed | same shape as #15 |
| 17 | subscription | `create_subscription`, `change_plan`, `subscribe_as_operator`, `change_plan_as_operator` | plan-version pin lookup against catalog over HTTP | routed (subscription slice), allow-listed | resolve the pin before the transaction opens, then write the subscription/change |
| 18 | auth | `api/routes.py:save_sso_config` | OIDC discovery via `IdpClient` | routed (auth slice), allow-listed | run discovery and validate endpoints untransacted; open the transaction only to seal the secret, write, audit and stage the event |
| 19 | notification | `api/engage.py:send_test_message` | SMTP via `Transport.send` inside `get_tenant_session` | routed (notification), allow-listed | claim (rate limit, load sender and connection), send with no transaction open, record the receipt and flip the sender usable in a second short transaction |
| 20 | notification | `connect_whatsapp`, `replace_whatsapp` (`whatsapp_provider.verify_ownership`, two Graph API GETs); `assign_conversation` (`memberships.membership_is_active`, HTTP to account) | httpx inside `get_tenant_session` | routed (notification — WhatsApp/Engage slices, merged), allow-listed | verify ownership / resolve the membership before the transaction opens (neither reads a row); open it only to seal and write, or to lock and assign |
| 21 | payment | `services.create_refund` | `PaymentProvider.refund` | routed (payment owner) | not flagged by the guard today because nothing calls it — there is no refund endpoint yet. The day one is added it must be split like `create_charge` (#3): stage a `refunds` row keyed `refund:{refund_id}` (already the provider idempotency key) in a short transaction, call with none open, record the provider's answer, and extend the stuck-charge sweep to resume a staged refund. The lease and lookup-before-create of #3 apply unchanged (Razorpay refunds are looked up by the payment and `notes.refund_id`) |
| 22 | account | `api/routes.py:verify_domain`, `cron.py:_recheck_one` | DNS TXT via `dns_check.check_txt` (`r = resolver or _default_resolver()`) inside the request / `session_scope` | routed (account — domain verification, merged), allow-listed | read the claim and its token, resolve with no transaction, re-lock and record — per domain in the recheck |
| 23 | lead | `put_assignment_rule`, `create_lead`, `update_lead` | `clients.invalid_owner_ids` (httpx to customer) inside `get_tenant_session` | routed (lead — CRM, merged), allow-listed | validate the owner ids (request input, not rows) before the transaction opens, then write; `:convert` already runs claim/call/record |
| 24 | notification | `connect_provider`, `replace_provider` | the dial guard (`dns_check.assert_dialable` → `resolve_public_addresses`) resolves the SMTP host inside `get_tenant_session` | routed (notification), allow-listed | resolve and vet the host before the transaction opens; the transaction only seals the credentials and writes the row |

## The filing contract (finance, rounds 2–3)

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
| e-invoice | `filed` / `failed` | outcome | record | registered / refused (or `retry_exhausted`). A `failed` row that carries `ack_no`/`ack_date` is a **failed withdrawal** — the registration still stands |
| e-invoice | `cancelling` | **claim** | `claim_irn_cancellation`, sweep lease | a withdrawal was asked for: reason code in `error_code`, remark in `error`, the acknowledgement it withdraws in `ack_no`/`ack_date`; actor in `created_by` and the `einvoice.cancel_requested` audit row. No IRN on the row (the `(org, irn)` unique index) |
| e-invoice | `cancelled` | outcome | record | withdrawn |
| e-Way | `submitting` | **claim** | `claim_eway_generation`, sweep lease | a generation was asked for; the row carries the transport facts, which are on no invoice |
| e-Way | `pending` | outcome | record (outage) | the portal did not answer; owed to the sweep |
| e-Way | `generated` / `failed` | outcome | record | a bill number and its computed validity / a refusal (`retry_exhausted`, `stale`). A `failed` row that carries `valid_from` is a **failed withdrawal** — the bill still stands |
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
`status: "cancelling"`, `terminal: false`, `remedy: null` on the invoice's e-invoice state. A
**failed withdrawal** is not "nothing to do": `terminal: false`, `cancellable_until` set, and
`remedy` is `cancel` inside the window or `contact_support` outside it (the portal may hold a
withdrawal the platform never saw confirmed). product-finance renders `cancelling` as "Cancelling
IRN" (in flight) and a failed withdrawal as "Withdrawal failed — the IRN still stands".

**Ordering.** An outcome is stamped `max(attempt clock, newest row's attempted_at)`, so a late
answer — a request whose call outlived the sweep's lease — is never sorted behind a row the sweep
wrote meanwhile. Every record step row-locks the invoice and re-reads the ledger; a registration
recorder that finds a live registration already standing (the consumer and the manual route
racing) returns it and appends nothing — no `uq_einvoice_filings_org_irn` violation, and no late
outage `pending` on top of a `filed` row.

### What the route returns

* cancel IRN / cancel e-Way: **201** with the newest row — `cancelled`, `failed` (portal refusal),
  or the `cancelling` claim itself when the portal was unreachable (the withdrawal is owed and the
  sweep finishes it).
* generate e-Way: **201** with `generated`, `failed`, or `pending` (outage).
* IMS sync: **200**; a portal outage is **503** and writes nothing.

### Who resumes what (`FilingReclaimSweep`, inside `finance-worker`)

| Found (cross-tenant discovery) | Discovered only if | Re-checked under the tenant binding | Action |
|---|---|---|---|
| reportable invoice with **no filing row** (`due_filing_reclaims`) | invoice older than 5 min; the organization **has a live GST registration**; no `filing_requested` staged since the invoice was created is still inside its backoff `resume_after(n)` | still no row; still auto-fileable; registration; request count `n` | `n ≥ MAX_FILING_ATTEMPTS` → terminal `failed` `retry_exhausted` (+ `filing_failed`, terminal); else re-publish `filing_requested` at attempt `n + 1` |
| e-invoice `pending` (`due_stale_filings`) | older than `pending_redrive_after(MAX_FILING_ATTEMPTS)` (37 min — the longest its own scheduled retry can be); registration; not backing off (as above, counted since the row) | `pending_redrive_after(k)`; registration; request count | exhausted → terminal `retry_exhausted`; else re-publish at attempt `k + 1` (the consumer's outage on the last attempt is terminal) |
| e-invoice `cancelling` | older than 5 min | `resume_after(n)`; **24-hour window from the acknowledgement** | window shut → terminal `failed` `window_closed` (no portal call); `n ≥ MAX_COMPLIANCE_RESUMES` → `retry_exhausted`; else fresh `cancelling` (the lease), call, record |
| e-Way `submitting` or `pending` | older than 5 min | `resume_after(n)`; **chain age ≤ 24 h** from its first row; registration | too old → terminal `failed` `stale` (no portal call); exhausted → `retry_exhausted`; else fresh `submitting`, call under the same key, record |
| e-Way `cancelling` | older than 5 min | `resume_after(n)`; **24-hour window from `valid_from`** | window shut → `window_closed`; exhausted → `retry_exhausted`; else fresh `cancelling`, call, record |

* **Nothing un-actionable reaches the batch.** An organization without a registration, and an
  invoice whose re-queue is still backing off, are excluded **in the discovery SQL**, not skipped
  under the tenant binding — so a handful of them can never fill `batch_size` and hide another
  organization's lost invoice (head-of-line blocking). `pending` rows inside their own retry
  window are not discovered at all.
* **Bounded backoff.** `resume_after(n)` = 5 min for the first re-drive, then the filing retry's
  own capped exponential (7, 11, 19, 35 min … capped at 63). The same function, in SQL, gates a
  re-queue.
* **Lease.** A claim re-drive appends a fresh claim row, so "newest row older than its deadline"
  is itself the lease; a re-queue's lease is the `filing_requested` outbox row it staged (read on
  the database's clock). The invoice is taken `FOR UPDATE SKIP LOCKED`, so one a request or another
  replica holds is skipped this tick rather than waited on.
* **A relay outage is not an attempt.** Only `filing_requested` rows the relay has **published**
  count toward `MAX_FILING_ATTEMPTS`; while any request for the invoice is still queued
  unpublished, the invoice is left alone (in discovery and in the re-check) instead of being
  re-queued behind it — so an outage cannot spend an invoice's budget and end it
  `retry_exhausted`. Tests: `test_reclaim_bounds.py::TestARelayOutageIsNotAnAttempt`.
* **Poisoned-step guard.** Every path ends: `MAX_FILING_ATTEMPTS` unanswered re-queues,
  `MAX_COMPLIANCE_RESUMES` claim drives, the statutory window, the 24-hour age cap. A tick that
  raises backs off (capped) and tries again — it shares a process with the consumer, and the
  worker's supervisor ends the process only when a loop *ends*. The clock is read per invoice.
* **Tests.** `services/finance/tests/test_reclaim_bounds.py` — `TestNoRegistrationIsNeverRequeued`,
  `TestAnUnansweredInvoiceIsBounded`, `TestDiscoveryIsNotHeadOfLineBlocked`,
  `TestTwoRegistrationDriversRecordOne`, `TestAgeAndWindowCaps`, `TestReadModelAndOrdering`,
  `TestTheStubEwayPortalAnswersRepeatsForReal`, `TestThePortalCallIsOffTheLoop`,
  `TestTheSweepReadsTheClockPerInvoice`; `test_worker.py` (both loops start, a returning loop ends
  the process, a poisoned tick does not end the consumer).
* **Discovery role.** The discovery queries run on `opsgrids_outbox` (BYPASSRLS), which holds
  **column-scoped** `SELECT` on exactly `invoices(organization_id, id, supply_type, document_type,
  status, created_at, deleted_at)`, `einvoice_filings` and `eway_bills(organization_id, id,
  invoice_id, status, attempted_at, created_at, deleted_at)`, and
  `gst_registrations(organization_id, id, is_primary, deleted_at)` — granted after every alembic
  run by `services/finance/migrations/env.py:grant_sweep_reader`, `REVOKE ALL` first. Every write
  runs on the app role under `session_scope(organization_id=…)`.

### Provider idempotency

| Call | Idempotent by | A repeat is answered with | Recorded as |
|---|---|---|---|
| `IrpClient.register` | the document (IRN = hash of GSTIN, type, number, FY) | `IrpDuplicate(ack)` | `filed` |
| `IrpClient.cancel` | the IRN | `IrpAlreadyCancelled(ack)` | `cancelled` |
| `EwayClient.generate` | `EwayPayload.idempotency_key` = `domain.eway_idempotency_key`: invoice, Part B (mode, vehicle, transporter, transport document — case- and space-insensitive), the two validity inputs (distance, over-dimensional) and the generation ordinal (count of earlier `generated` rows + 1) | `EwayAlreadyGenerated(ack)` | `generated` with the portal's original number |
| `EwayClient.cancel` | the bill number | `EwayAlreadyCancelled(ack)` | `cancelled` |
| `ImsClient.fetch` | read-only | — | ingest is idempotent on `(organization_id, source_ref)` at the database |

A real adapter must map the portal's "already done" error codes onto these exceptions rather than
onto `…Rejected`; that mapping is what turns a crash-then-resume into a success instead of a
spurious failure. Each `record_*` is also idempotent on our side: the invoice is row-locked, the
newest row re-read, and an outcome that somebody else already recorded (the same bill number, a
`cancelled` row, a claim already resolved) appends nothing.

The stub e-Way client numbers a bill **from its idempotency key** and remembers what it issued, so
a resumed generation is answered `EwayAlreadyGenerated` by the stub itself — the "already
generated" path runs for real in tests, not only through a fake.

## The payment contract (round 3)

Razorpay has no idempotency key on a payment create (ADR-0004 §6 and its addendum), so:

* **Lookup before create.** `call_provider_charge` ensures the charge's provider Order
  (`receipt` = the charge's own id — 36 characters, unique, stable; Razorpay caps receipts at 40
  and does not make them unique), asks for a payment already made against that Order, and only
  then creates one. `charges.provider_order_id` records the Order. A resumed call finds the
  payment a crashed one made.
* **One call at a time.** `charges.attempts`, `next_attempt_at`, `lease_until`: the stager holds
  the lease from insert; anyone else — the caller's own HTTP retry, the sweep — takes it only by a
  conditional `UPDATE … RETURNING` once it has lapsed and the backoff has passed. A retry of an
  in-flight key gets the charge as it stands and makes no provider call.
* **Finalize never regresses.** The charge is locked and re-read; the provider payment id is set
  only if NULL (a different one is logged and audited `payment.charge.provider_id_mismatch`, never
  written); state moves only from `created` and only to a non-terminal state — captured/failed come
  by webhook with their events. `payment.charge.staged` is audited at insert and
  `payment.charge.created` once, by the first recorder.
* **The sweep is bounded, and never fails a charge blind.** It discovers only charges with no call
  in flight and not backing off (in SQL, so none can block the batch) and claims the lease. At
  `charge_max_attempts` (5) it makes a **lookup-only** call (`find_order` by receipt, then
  `find_payment` — never a create): a payment found is finalized; none found fails the charge
  (`attempts_exhausted`, re-checked under the row lock) and emits `commerce.payment.failed` so
  dunning proceeds; a lookup that cannot be made (timeout, error) is retried
  `charge_final_lookup_attempts` (3) times, then the charge fails as `lookup_unavailable` with an
  alert log. Discovery reads a column-scoped grant on `charges`
  (`selectors.SWEEP_READER_CHARGE_COLUMNS`).
* **The webhook takes the same lock and records what finalize could not.** It resolves the charge
  `FOR UPDATE` and re-reads it, so it never acts on a state the sweep or finalize just changed (no
  second `commerce.payment.failed`, no `captured` written over the sweep's commit). It records the
  provider payment and Order ids from the verified body when the charge has none — a call that
  outlived its timeout was never finalized, and its capture would otherwise leave a charge no
  refund can reach. A `payment.captured` for a charge the platform failed on its own judgement
  (`attempts_exhausted`, `lookup_unavailable`) is recorded (`failed -> captured`, audited
  `payment.charge.captured_after_local_failure`, logged at error). So is a `payment.captured` for
  **the same payment** the provider had reported failed — a bank's late authorisation, failed →
  authorized → captured (`payment.charge.captured_after_provider_failure`, the authorization itself
  logged at error as `payment.webhook.late_authorization`); it emits `commerce.payment.captured` as
  any capture does (ADR-0004, PAY-A addendum — *owner to confirm*). A `captured` that is neither is
  kept in the inbox as a `conflict` (`illegal_transition`, or `provider_id_conflict` when it names
  another payment id than the charge holds), audited `payment.webhook.ignored` and answered **200**
  with the charge untouched, not a 422 the provider would redeliver for days. The same holds for a
  payment in another amount or currency than the charge and a charge that is not the
  organization's: reconciliation runs in a SAVEPOINT, raises a typed verdict for each, and the row
  is recorded once more stating why. Every by-id charge query filters on the organization, not on
  RLS alone. Every capture also checks whether another charge of the same invoice is captured too
  (`_detect_double_collection`, `ix_charges_org_invoice`) — a dunning retry and the same charge's
  own late capture can both settle one invoice, in either order — and logs and audits
  `payment.invoice.collected_twice` with no automatic refund; `charge_on_dunning_retry` also checks
  *before* staging, so a settled invoice is not debited a second time at all. Non-2xx is exactly: a
  bad signature or an unidentifiable body (**400**), a transient database failure (**503**), an
  unrelated `IntegrityError` (the platform's **409**), and a defect (**500**) — nothing else, and
  nothing here ever turns a defect into a permanent condition recorded as `ignored`.
* **The lease outlives the call.** `charge_reclaim_after_seconds >= 2 × provider_call_timeout_seconds
  + 60` is validated at start-up (finance: `2 × portal_call_timeout_seconds + 60 ≤
  FILING_RECLAIM_AFTER`), and an adapter's per-request connect/read timeouts over `ensure_order`
  + `find_payment` + `charge` must sum under `provider_call_timeout_seconds` — otherwise an
  abandoned call could land its create after the sweep's lookup found nothing.
* **The webhook's idempotency key is a header.** Razorpay's body has no top-level `id`; the inbox
  deduplicates on `X-Razorpay-Event-Id`, else a body id, else `derived:` + a digest of the event
  name, entity id and `created_at`, and refuses a webhook with none — never `''`
  (`domain.resolve_event_id`; a database CHECK backs it).
* **Adapters are held to the contract by a test.** `payment/tests/provider_contract.py::
  ProviderContract` — `ensure_order` idempotent by receipt, `find_order` never creates,
  `find_payment` by Order, create at most once per receipt, declared per-request timeouts summing
  inside `provider_call_timeout_seconds`. The stub passes it (`test_provider_contract.py`, which
  also requires each check to fail against an adapter broken the way it guards against); the live
  adapter (B5g) subclasses it and passes it unchanged.
* **Tests.** `payment/tests/test_charge_final_lookup.py` — `TestTheFinalLookup`
  (`test_exhaustion_finalizes_a_charge_whose_last_attempt_made_a_payment`, none-found,
  lookup-unavailable, lookup-timeout), `TestALateCaptureIsRecorded`,
  `TestTheWebhookRecordsTheProviderId`, `TestTheWebhookAndTheSweepSerialize`
  (`test_webhook_failed_vs_exhaustion_race_double_failed`), `TestTheLeaseOutlivesTheCall`,
  `TestTheOrderReceipt`. PAY-A: `test_webhook_event_identity.py` (two events for one org both
  processed on a real, id-less body; redelivery deduplicated; header-less digest; an empty key
  impossible in the service and in the database), `test_webhook_permanent_conditions.py` (each
  permanent condition answered 200, kept, audited, no state change; a transient failure is a 503;
  a redelivery of an ignored event is deduplicated), `test_webhook_domain.py`,
  `test_provider_contract.py`. PAY-A fix round: `test_webhook_late_authorization.py`,
  `test_webhook_provider_id_binding.py`, `test_webhook_tenant_scope.py` (on a BYPASSRLS session),
  `test_webhook_event_id_reuse.py`, `test_webhook_failure_classification.py`.
