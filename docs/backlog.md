# Backlog

What is next, in order, and what is blocking. Delivered work is at the bottom.

> This list previously lived in the build harness's ledger under `.claude/`, which is local
> tooling and no longer tracked in the repository. It lives here now so it ships with the code.

Each item is a **slice**: a capability that crosses the whole stack — schema, API, UI, tests —
rather than a horizontal layer. Nothing is "done" until its tests pass.

---

## Inventory & manufacturing (Operations)

Phased plan: [inventory/plan.md](inventory/plan.md), decisions in
[ADR-0014](adr/0014-inventory-and-manufacturing.md). **Phase 0 done** (ledger, milli-unit
quantities, posting kernel, reconciler — [inventory/ledger.md](inventory/ledger.md)).
**Next: Phase 1** — item master, UOMs, lots, rolls, locations (scope and exit criteria in the plan).

### Warehouse map & storage put-away — implemented on branch, pending owner review, not deployed

Branch `feat/warehouse-map-putaway` (design: [the spec](superpowers/specs/2026-10-05-warehouse-map-putaway-design.md);
decisions: [ADR-0028](adr/0028-putaway-gps-captures.md) and the amendment to ADR-0014 §12; migration `d7a3c9e5b184`;
rollout, rollback and the purge: RUNBOOK, "Warehouse map and storage put-away"). **Built:** a site map and 2-D/3-D
floor view over the published layout, a find-stock search (`locate`: at most 500 bins, says when it cut), and a
worker-app **Put away** screen: scan the destination bin (the suggestion carries the bin's barcode, and only bins that
have one are suggested; the picker offers only stock in `receiving`/`staging` bins, from the new `location_kind` on
stock rows), with the device's GPS fix kept 30 days as audit evidence and revealed only through an audited,
ADMIN-only, one-time-code route. The review rounds are folded into the spec as marked amendments. **Nothing is
deployed**; merging deploys nothing, and the owner approves any deploy. Open items, owner decisions first:

- **Owner decision: keep the one-time code on the reveal, or revert.** The reveal needs a session that gave a one-time
  code (`amr={"otp"}`), which was added in review **beyond the owner's literal D3** (permission plus audit). Keeping it
  means an administrator with no authenticator, or on a single-sign-on or social-login session, gets a 403. Reverting is
  one line plus flipping `test_putaway_authz.py::TestTheRevealNeedsASessionThatGaveAOneTimeCode` (ADR-0028, "Owner
  decision"). Follow-up if kept: a step-up prompt in the operations SPA and then the `stepup` marker on the route
  (ADR-0023 risk 5), so a code is proved *for this reveal*.
- **Owner: Google Cloud setup (the Maps key).** The Google Cloud project, billing, spend cap, the referrer and API
  restrictions, and the `VITE_GOOGLE_MAPS_API_KEY` line in the box's `.env`. The release does not wait for it: without
  the key the operations bundle publishes map-less with a loud warning, and the reveal is text only. Adding it later is
  a `make edge-web ... APP=operations` republish. Know that with a key the reveal's map discloses the viewport position
  to Google (ADR-0028); there is no per-click text-only choice today.
- **Permission reconcile after the API deploy.** `make edge-reconcile ... PRODUCTS=operations` (dry run, then
  `APPLY=1`) so `OPERATIONS_ADMIN` gains `operations.inventory.putaway.location_read`; the reveal answers 403 in
  existing organizations until it runs.
- **Two-stack re-parent.** Two unmerged inventory stacks share head `c4a8e1f6d273`; whichever merges second re-points
  `d7a3c9e5b184`'s `down_revision` (marked `# RE-PARENT HERE`) and confirms a single `alembic heads`.
- **D1 against the warehouse's reality.** Put-away is allowed only from `receiving` or `staging` bins, and the order
  engine reserves only at an item's home bin (limitation (c)), so stock put away elsewhere is invisible to order
  accept. On `main` a goods receipt lands in the item's home bin (kind `bin`), so stock reaches a receiving/staging bin
  only by opening stock posted there, an explicit bin transfer, or Phase 2 receipts (not merged). Decide whether the
  warehouse sets up receiving/staging bins first, and schedule multi-location allocation, which lifts (c).
- **The weekly purge (owner).** `tools/purge_putaway_captures.py` is run by hand, weekly, dry run first; nothing runs it
  for you. The purge writes no audit row of its own: revisit if an auditor wants a tamper-evident erasure log.
- **A swallowed capture failure has no alert.** A put-away whose capture row cannot be written still answers 201
  (`geo_not_recorded`) and logs `putaway.capture_failed`; `ops-monitor` has no log-based check, so the RUNBOOK gives a
  manual log count and a durable SQL check to run after the deploy and daily for the first week. Wiring the log line
  (or that query) to an alert is open.
- **Server `occurred_on` has zero clock tolerance.** `PutawayIn` refuses an `occurred_on` after the API process's own
  `date.today()`, with no tolerance. The apps now send it only for a put-away confirmed offline (the UTC date of the
  confirm; online the server dates it), so only an offline confirm can be refused (a phone clock ahead of the
  server's, or an API container whose local date is behind UTC), and it is parked with a message to fix the phone's
  date. A day of tolerance on the server, or ignoring the client's date, would remove it.
- **The foreground put-away is not persisted before the geolocation wait.** Confirming waits for the position (5 s, plus
  a 1 s guard) *before* the entry is saved to the queue, so a page closed or killed in those seconds loses the
  put-away it just confirmed. Saving the entry first and attaching the fix after is not done.
- **The 2-D floor plan makes every bin a tab stop.** Each placement is a focusable `role="button"`, so a floor with
  thousands of bins is thousands of tab stops and a heavy DOM. A roving tabindex (one stop, arrow keys inside) and
  windowing the rectangles are the fix; it has not been measured at a real floor's size.
- **The put-away 422 messages tell a worker about a bin they cannot read.** A worker holds no `location.read`, yet a
  refused scan says what the barcode resolved to: "…not a {kind}" (`destination_not_storable`) and "A put-away stays
  inside one warehouse" (`different_warehouse`). Low severity (a barcode is a printed label), but it is a probe of bin
  kind and warehouse by a role that cannot list locations; a message that does not distinguish the cases would close it.
- **CSP hardening (separate release).** `ops` and `worker` send no `Content-Security-Policy` today; when one is
  added it must allow the Google Maps origins.

## Now — as of 2026-09-22 (`main` @ `a60baa1`)

This section is the current order. The sections below it keep their history and have been
corrected where they had gone stale; nothing had edited this file since #1.

**All ten products and Engage are live and sold** on the single edge VM (ADR-0012, ADR-0013),
deployed only by GitHub Actions from green `main`.

| # | Slice | Status | Why this order |
|---|---|---|---|
| 1 | **Storefront custom-domain sweep** (`services/storefront/app/cron.py`) | In progress | The module now exists (ADR-0012 §6) and, since ADR-0013's memory-headroom fix round 2, runs inside the `storefront` API container (`RUN_SWEEPS_IN_API`) — `storefront-cron` is no longer a container. Original gap: a merchant's custom domain could never become verified. ADR-0012 specifies the behaviour. |
| 2 | **Document service and MinIO on the edge** | PR open | `document` and `minio` are wired into `compose.prod.yml` behind the `storefront` profile, routed at the gateway, migrated by `remote.sh`, and backed up; needs `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` set on the box before the next deploy. |
| 3 | **Scheduler service, first consumer: finance `not_before`** | Not started | `services/scheduler` is a scaffold. e-Invoice and e-Way retries carry their own delay and are redelivered at once instead. Per-service cron containers move onto it later. |
| 4 | **GSTR-3B and the input-tax-credit ledger** | Not started | The next compliance slice that needs no credentials, using the stub-client pattern. Larger than 1 to 3. |
| 5 | **Off-box backup** | Code done, not enabled | `backup.sh` uploads to S3 once `BACKUP_S3_*` is set in the box `.env`. The owner must provide the bucket. |
| 6 | **HRMS: leave, payroll, recruit** | Not started | employee and attendance are live. |
| — | **IRP / GSP / e-Way / IMS / Razorpay live wires** | Blocked | Each needs credentials from the owner; the state machines are done. |

**Delivered since this file was last edited** (#2 to #25):
- notification service and event-bus wiring (#2)
- WhatsApp (#4, #5, #9 to #13):
  - template sends
  - sealed contact phone numbers
  - phone lookup without decrypting
  - delivery and read receipts
- CI sharding, cutting a run from about 10.6 to about 3.5 minutes (#6 to #8)
- Storefront (#14):
  - delivery serviceability
  - store settings
  - production edge
  - buyer bundle
- single edge relay process, and PulseBoard on the real API (#15)
- write surfaces for seven products (#16)
- Engage, slices through to buyable (#16 to #18, #21, #22)
- CRUD across eleven products, plus the security review, cross-tenant sweep and HRMS PII envelope (#19)
- permission-cache fix after activation (#20)
- deploy hardening (#23, #25):
  - the gateway and Caddy ride out a container restart instead of answering 503
  - the relay supervisors wait until they are up

---

## Blocked, and first

### Row-level security
**[ADR-0002](adr/0002-row-level-security.md) is accepted and its four open questions are
answered.** In progress, in two slices.

**Slice one — prerequisites, no policies: DONE** (`c708f60`, `<pending>`).

Services connect as per-service NOBYPASSRLS roles and migrations as an owner role; grants are
applied by the migration run so a new service cannot ship without them; TRUNCATE and
audit-rewrite are withheld by privilege; the test suite runs as the app role so the split is
visible to it. The tenant chokepoint opens each request's transaction and binds it to the
caller's organization as the first statement — routes no longer open transactions of their own.
auth's principal routes (switch-org, me, MFA enrolment, password change) are on the chokepoint
too, and outside production the binding is read back and asserted immediately after it is set.

Exit gate met: the GUC is set on every transaction, it does **not** survive the transaction,
and a static check refuses any route that takes a bare session or opens its own transaction.
Verified by deliberately introducing a bypass and watching the guard fail.

A correction to the record: an earlier commit message said migrations had been "silently doing
nothing". They had not — `alembic` exits 255 on failure and `make migrate` surfaces it. That
claim came from reading the exit code of `tail` through a pipe rather than of `alembic`.

**Slice two — policies: DONE.**

Row-level security is live on the `account` schema. Eleven tables carry a `tenant_isolation`
policy with both `USING` and `WITH CHECK`; `users`, `permissions` and `processed_events` are
exempt for the reasons ADR-0002 records. `roles.organization_id` is NOT NULL and the template
machinery is gone. The cross-tenant reads that sign-in depends on run on a third role —
BYPASSRLS but SELECT-only on seven identity tables — so they keep working without handing the
migration role to a module that only reads.

`test_cross_tenant_isolation.py` is the acceptance test: a bound session sees only its own rows,
cannot read another tenant's row by primary key, cannot write into another tenant, and an unbound
session sees nothing at all.

**Closed since (27b3b1a):** the generated per-route cross-tenant sweep
(`services/account/tests/test_cross_tenant_sweep.py`) and the tenant-leading index recomposition
(`46478181a5ab`). The paragraph below is kept for the record. Previously open: the sweep, and the index recomposition
ADR-0002 lists (`audit_logs` first — its five single-column indexes no longer lead with the
tenant column now that the policy qual is ANDed into every query). Neither blocks correctness;
the second is a performance cliff waiting for volume.

The relay and the stall probe now run on `opsgrids_outbox` (BYPASSRLS, SELECT and UPDATE on
`outbox` only), so neither is blind to the rows the app role can no longer see — which both were
the moment the policies landed, since each connected as the app role with no tenant bound.

---

## Next

### GST compliance engine — returns, e-Way and withholding have landed; the credentials remain

**Why this is first.** India's e-invoicing mandate reaches every business above ₹5 crore
aggregate annual turnover from April 2026, and the window for reporting an invoice to the Invoice
Registration Portal is tightening toward three days from the document date. Three days is not a
deadline a person meets by remembering; it is a deadline a system meets. That makes compliance the
platform's chosen primary differentiator — "Compliance Autopilot" — and this the slice everything
else in it stands on.

**Why it is a structural advantage rather than an integration.** Anyone can call the IRP. What is
hard to copy is that OpsGrids's invoices were already append-only with corrections as credit notes
(ADR-0000 §9), which is *exactly* GST's own legal model: a filed document is never edited, and a
correction is a new document that references it. The two models line up with nothing left over, so
every filing action can be a provable, reversible ledger row instead of a status column somebody
has to trust.

**Slice one — the foundation: DONE.**

- **GSTIN is a value object, not a string.** `domain.parse_gstin` checks length, structure, a real
  state code, and the government's own folded base-36 check digit; it returns a `Gstin` carrying
  the state code, PAN and entity code. Anchored to two public GSTINs, and property-tested: every
  single-character corruption and every adjacent transposition of a valid number is caught.
  Customer GSTINs are verified on write, so this service stores no unverified one.
- **The seller's identity is a finance-side config row.** `gst_registrations` — per state, one
  primary, with `state_code` *derived from* the GSTIN rather than entered beside it. A partial
  unique index makes "at most one primary" a database guarantee.
- **Documents carry their tax facts.** Place of supply, supply type (B2B/B2C/export/SEZ),
  reverse-charge, both parties' GSTINs as snapshots, and per-line HSN/SAC plus per-line rate.
- **The split is integer arithmetic that foots exactly.** `domain.split_gst` computes the tax
  once and then divides it — CGST+SGST intra-state (odd paisa to SGST, in both signs), IGST
  inter-state, with export and SEZ always inter-state by statute. Rounding each half
  independently is the bug the shape exists to prevent. Property-tested across every band and a
  range of amounts from one paisa past the 32-bit boundary; a credit note reverses an invoice
  head for head.
- **`einvoice_filings` is append-only at the database.** One row per attempt — `pending`, `filed`,
  `failed`, `cancelled` — never mutated, so a document's filing history is a query rather than an
  archaeology exercise. Enforced by a `BEFORE UPDATE OR DELETE` trigger, because a privilege
  `REVOKE` does not survive `apply_schema_grants` re-granting DML after every migration run; the
  platform learned that on payment's `refunds` and it is the same shape here. Under RLS like
  every other tenant table.
- **The portal is a `Protocol` with a deterministic stub.** `IrpClient` speaks the document; the
  stub computes the **real** IRN (`SHA-256(supplier GSTIN + doc type + doc number + FY)`) and
  refuses documents the way the portal does, with numbered error codes. No network, no
  credentials — the same bargain payment's Razorpay adapter strikes, so the state machine and the
  persistence are real and testable today and only the wire is left.
- **Endpoints.** Filing status on the invoice read model (`einvoice`, never null, so an unmet
  obligation is visible rather than absent), `GET /finance/invoices/{id}/filings` for the history,
  `GET`/`PUT /finance/gst/registration`, and internal `POST /internal/v1/einvoice/filings` and
  `…:cancel` for the auto-filing agent. New permissions `finance.compliance.einvoice.read` and
  `finance.compliance.registration.read/write`, the last marked dangerous.
- **Events.** `finance.compliance.invoice.filed`, `.filing_failed` (carrying `retryable`, so an
  outage is retried and a rejection is not) and `.filing_cancelled`. `finance.billing.invoice.issued`
  now carries `requires_einvoice` and `place_of_supply`, so an agent can decide from the event
  alone without reading back.
- The demo book is GST-complete: a Maharashtra seller registration, checksum-valid customer
  GSTINs, HSN/SAC on every line, and both split shapes exercised on real data.

**Slice two — autopilot, and the queue that comes back: DONE.**

- **Filing no longer waits for a button.** Finalizing a reportable document stages
  `finance.compliance.invoice.filing_requested` on the outbox — in the same transaction as the
  invoice, so the attempt is sent only once the document is durable and never inside the request
  that raised it. Tying a customer's ability to bill to a government portal's uptime is the
  failure this shape exists to prevent. A consumer picks the request up and files it through
  `IrpClient`, persisting the IRN, the acknowledgement number and date, and the signed QR verbatim.
  `domain.should_auto_file` adds the lifecycle the statutory test does not carry: a draft has not
  been issued and a void has been withdrawn, and since an IRN is a hash of the document number and
  financial year, filing either burns the number the real document will need. The internal route
  remains for a document somebody names deliberately, and is not held to that second test.
- **A document already carrying an IRN is never filed twice.** Idempotency at the event id is not
  enough — a retry that crossed with a manual filing, or a second finalize, is a *different* event
  naming the same document — so `services.live_registration` is consulted **before** the client is
  touched. The portal answers a duplicate registration with `2150`, and discovering a fact you
  already had by being refused is how an integration files every invoice twice under load. Both
  the manual route and the consumer go through the same guard, and the test asserts on the portal
  *call count* rather than on rows.
- **Failure is bounded and it surfaces.** A rejection is terminal at the first attempt: the same
  bytes will be refused again. An outage is retried on an exponential, capped backoff up to
  `domain.MAX_FILING_ATTEMPTS`, and when the budget is spent the outage becomes a terminal `failed`
  row carrying `retry_exhausted` — the platform's own error namespace, kept distinct from the
  portal's numeric one so a support conversation is never ambiguous about who refused what. The
  read model carries `terminal`, so "the automation gave up" is something a screen says rather than
  something an operator infers.
- **The cancellation window is modelled from both ends.** Inside 24 hours a withdrawal is a
  `cancelled` filing row; outside it the service refuses and names the instant the window closed,
  pointing at the credit note — which the append-only invoice model already supports, so nothing is
  lost when the window shuts. The deadline is arithmetic over the stored `ack_date`, and the read
  model exposes it as `cancellable_until` plus a single `remedy` (`cancel` or `credit_note`, never
  both) so a screen offers the one that will work.
- **IMS reconciliation, the recurring half.** `ims_documents` holds what suppliers filed against
  this organization, keyed on the **portal's** row reference rather than the supplier's document
  number — which is what lets a re-sync create nothing while leaving a genuine duplicate
  representable. `purchase_documents` is the local counterpart a match is made against, thin on
  purpose, with its tax split computed by the same `domain.split_gst` an invoice uses.
- **The reconciler suggests and never decides.** Per document it produces an action *with its
  reason*: matched exactly, an amount mismatch with the delta signed and named in paise, a supplier
  nothing has been bought from, a known supplier with no such number, or a duplicate of an earlier
  arrival. Duplicate is evaluated first — a second copy of a document that already matched a
  purchase order still matches it, so a match-first ordering would suggest claiming the same credit
  twice. Nothing moves a document's status; even the exact match stays `pending` until a person
  confirms, because accepting claims the input tax credit on somebody else's invoice.
- **`ims_decisions` is append-only at the database.** Who decided, when, what the platform had
  suggested at the time, and whether they followed it. A change of mind is a further row. Enforced
  by trigger for the same reason `einvoice_filings` is — that is now twice the platform has walked
  into `apply_schema_grants` undoing a `REVOKE`. `ims_reconciliations` is pointedly *not* frozen:
  a suggestion is recomputed opinion, and freezing an opinion buys an auditor nothing.
- **Endpoints.** `GET /finance/ims/documents` (filters: status, supplier, period, suggestion
  reason, free text), `GET /finance/ims/documents/{id}` returning the evidence behind the
  suggestion — the matched purchase document, the earlier copy a duplicate duplicates, the decision
  history — and `POST /finance/ims/documents/{id}/decisions`. Internal `POST /internal/v1/ims/sync`
  ingests a period and re-runs the reconciliation. New permissions
  `finance.compliance.ims.read` and `finance.compliance.ims.decide`, the second marked dangerous.
- **Events.** `finance.compliance.invoice.filing_requested` (the trigger, and the retry),
  `finance.compliance.ims_document.ingested` and `.decided` — the last carrying
  `followed_suggestion`, because a suggestion nobody follows is one that should be changed and that
  is only knowable if the disagreement is written down.
- **The second portal is stubbed like the first.** `ImsClient` speaks documents; `StubImsClient`
  computes five fixture documents whose tax comes from `domain.split_gst` against the *recipient's*
  state, so the same fixture is CGST+SGST for a Maharashtra buyer and IGST for a Gujarat one. No
  network, no credentials, asserted by a test that forbids opening a socket.

**Slice three — the returns, the road, and the money withheld at source: DONE.**

- **GSTR-1 is assembled, not transcribed.** `domain.assemble_gstr1` builds a period's outward
  supplies from the invoices already recorded, and **nothing in it computes tax** — every figure
  is a sum of the splits the invoices already carry, so the return foots to its own documents by
  construction rather than by a reconciliation step somebody has to remember to run. Every
  reported document lands in **exactly one** section (EXP, then CDNR/CDNUR, then B2B, then B2CL,
  then B2CS), which is what makes the footing provable: two sections would double a figure and
  none would lose one. The sections are the real ones — B2B and B2CL invoice-wise, **B2CS
  aggregated** by place of supply and rate because that is what the return declares, CDNR carrying
  the number each note corrects, EXP, an **HSN summary** built in parallel from the same lines
  rather than from the section entries, and a **document series** where a void's number is
  declared and counted as cancelled even though its figures are not a supply.
- **The footing is proved three independent ways.** Sections against the lines, the HSN summary
  against the lines, and the one-line totals against both. The HSN summary and the totals each
  start from the lines rather than from the assembled sections precisely so that comparing them is
  a real cross-foot and not a tautology — the portal cross-foots them too. Property-style across
  every rate band and a range of amounts from one paisa upward, in both split shapes.
- **A filed return is frozen at the database.** `gst_returns` holds one row per (registration,
  period, type) with the snapshot as JSONB; `gst_return_transitions` is the append-only status
  trail the `status` column is only the head of. A trigger refuses any change to the snapshot,
  the totals, the period — or `deleted_at`, since a soft delete is still an UPDATE and would
  otherwise be the last route to erasing what was reported. So a correction arriving in September
  shows up through `GET .../diff` as a **difference**, per section and in total, which is what an
  amendment in a later period declares. The boundary is `filed` rather than `generated` on
  purpose: before it goes to the portal nothing has been reported to anybody, and re-assembling a
  period after a late invoice is the *point* of looking at a return before sending it.
- **Generating and filing are separate permissions.** Generating states what this platform
  believes about its own book and can be repeated; filing makes a claim to a government and is
  terminal. Only the second is marked dangerous, and a test asserts neither grant implies the
  other in either direction.
- **e-Way bills, and validity the platform can explain.** `eway_bills` is one row per attempt with
  the same append-only-at-the-database shape as `einvoice_filings`. The obligation starts above
  50,000 rupees of consignment value — strictly, so the invoice landing exactly on the threshold
  is tested at the rupee. `EwayAck` deliberately carries **no deadline**: one day per 200 km *or
  part thereof*, each day expiring at midnight of the day immediately following generation,
  computed by `domain.eway_valid_until` from the `distance_km` and `valid_from` the row
  **stores**. A bill raised at 23:50 for 150 km is therefore valid for ten minutes, which is the
  statute and which a screen saying "valid for a day" would be lying about. A deadline only the
  portal knows is one nothing can explain and no test can check, and the cost of a screen and a
  checkpoint disagreeing falls on a driver.
- **`expired` is a read, not a write.** The table is append-only, so nothing can update a row when
  the clock passes it; `effective_status` derives it from the stored `valid_until`. Cancellation
  is the same 24-hour window as an IRN, enforced against the stored `valid_from` — but with no
  credit-note fallback, because the movement happened and has to be reported as it happened.
- **GST-TDS and the income-tax pair, as pure integer functions.** Section 51 is a **cliff**: 2 % of
  the *whole* taxable value above 2,50,000 rupees, split across CGST/SGST/IGST by reusing
  `domain.split_gst` so a deduction cannot round differently from the levy beside it. 194Q and
  206C(1H) are **aggregates** over a financial year with one counterparty, biting only on the
  excess, so the invoice that crosses 50 lakh is withheld on partially — the case a naive
  implementation over-deducts on by lakhs. They are mutually exclusive by statute and the buyer's
  194Q liability wins; charging both is the error "apply every matching rule" produces. Every
  boundary is tested at the exact rupee.
- **Rates are versioned and do not restate history.** `withholding_rates` joins `tax_rates` as
  global, unpolicied vocabulary — a 2 % deduction under s. 51 is the law of one country, not one
  tenant's data. A change is a new row with a later `effective_from`; resolution is against the
  **document's** date; and the resolved rate and threshold are snapshotted onto the register row
  beside the id of the version they came from. A test publishes a future rate, re-derives an
  August invoice, and gets August's answer.
- **Withholding is opt-in by construction.** Assessment runs inline at finalize (arithmetic over
  rows already in the transaction, unlike filing, which calls a portal and therefore goes through
  the outbox), gated on three flags on the customer that all default false. Whether a body is
  notified under s. 51, and whether a buyer is liable under 194Q, are facts about somebody else's
  registration and somebody else's books; the platform cannot infer them, so it does not guess.
- **Reporting.** `GET /finance/gst/withholdings/summary?period=` groups the register **per
  counterparty**, which is how GSTR-7 is declared — the shape that makes these figures comparable
  to the return a deductor actually files, and therefore reconcilable. The totals sum the grouped
  rows exactly, and a test says so, because a summary that does not foot to its own rows is worse
  than no summary.
- **Endpoints.** `GET/POST /finance/gst/returns`, `GET /finance/gst/returns/{id}` (with every
  section), `GET .../diff`, `POST .../filing`; `GET /finance/eway-bills`,
  `GET /finance/invoices/{id}/eway-bills`, `GET /finance/invoices/{id}/eway`;
  `GET /finance/gst/withholdings`, `.../summary`, `GET /finance/gst/withholding-rates`; internal
  `POST /internal/v1/eway/bills` and `...:cancel`. Six new permissions:
  `finance.compliance.returns.read/generate/file`, `finance.compliance.eway.read` and
  `finance.compliance.withholding.read`.
- **Events.** `finance.compliance.gst_return.generated` and `.filed`,
  `finance.compliance.eway_bill.generated`, `.failed` (carrying `retryable`, like the filing
  events) and `.cancelled`, and `finance.compliance.withholding.recorded` — announced because the
  money it names never arrives, and a collections consumer expecting the gross would chase a
  receivable that was settled net.
- **The third portal is stubbed like the first two.** `EwayClient` speaks consignments;
  `StubEwayClient` refuses what the real portal refuses with its numbered codes and opens no
  socket, which a test asserts. Its one honest deviation is called out in its own docstring: the
  real portal issues numbers from a sequence, and this one keys them on the document **and the
  moment the bill was raised** — deterministic in its inputs, but not one number per document,
  because a document legitimately carries several bills over its life and keying on the document
  alone would collide on the unique index and strand a lorry.

**Still open, in the order the market forces it:**

| Next | Why it is next |
|---|---|
| **IRP / GSP / e-Way credentials and the wire** | The remaining seam on all three portals, and now the *only* one: the state machines, the retry policy, the idempotency guards, the validity arithmetic and the persistence are real and exercised. `IrpClient`, `ImsClient` and `EwayClient` are the whole change surface. e-Way needs its **own** credentials — NIC runs it as a separate product with separate downtime, which is why it is a separate client rather than a method on the IRP one. |
| **GSTR-3B** | The summary return GSTR-1 feeds, and the one that actually settles the liability. It needs the *inward* side as well: the IMS decisions decide what input tax credit may be claimed, and GSTR-3B is where the two halves net. Every input exists; nothing about it is assembled yet. |
| **The input-tax-credit ledger** | GSTR-3B's other prerequisite, and a ledger in its own right: credit accrued from accepted IMS documents, credit utilised against output liability, and the balance carried forward. Append-only like every other ledger here, and the place a wrongly accepted supplier document eventually costs money. |
| **Returns in the dunning loop** | An unfiled return past its deadline is a business risk exactly as an overdue receivable is, and the dunning and grace policy already knows how to escalate one. Wiring `gst_return.generated`/`.filed` — and their *absence* — into it is a policy change rather than a new mechanism; nothing chases a return today. |
| ~~**GSTR-7 as the deductor**~~ **DONE** (`gstr7` return type, `test_withholding.py`) | This slice reports withholding from the **supplier** side: what was deducted from what this organization is owed, grouped per deductor so it reconciles against the return they file. Filing GSTR-7 as the *deductor* is the mirror image, measured over `purchase_documents` rather than `invoices`, and it is a second obligation with its own window. |
| **A scheduler that honours the backoff** | `filing_requested` already carries `attempt` and `not_before`, so the retry knows its own delay; what does not exist yet is something that waits for it rather than redelivering at once. An e-Way outage now wants the same thing, and more urgently — the goods are already loaded. A delivery change, not a redesign. |
| **IMS credentials, and pushing actions back** | Reading a supplier ledger and recording a decision about it are done; telling GSTN what was decided is a second integration. `ImsClient` deliberately has no `submit()` — inventing a method nothing implements would read as finished. |
| **B2C dynamic QR** | The obligation B2C carries instead of an IRN. Deliberately out: `requires_einvoice` already answers `false` for it, so it is additive. |

Seven things these slices deliberately did **not** do. Historical documents were not back-split
into tax heads: which head a supply was taxed under depends on a seller state that no column
recorded when those documents were frozen, and deriving it now would be inventing a legal fact
rather than recording one. UIN/OIDAR registrations are refused rather than half-supported.
`purchase_documents` is a reconciliation counterpart, not an accounts-payable lifecycle — there is
no approval, no three-way match, no payment run. Nothing auto-accepts an IMS document, however
exact the match, because the credit that follows is a person's to answer for. **Nothing files a
return by itself**: generating could be automated, filing is a person's claim to a government, and
collapsing the two would mean nobody ever looked at a period before it went. **No route lets a
person type a withholding in** — a figure somebody could type is a figure nobody can trust, so the
register is only ever computed at finalize, and a deduction the platform cannot see stays out of
it rather than being invented. And **an e-Way bill is never raised automatically**: the transport
facts are not on an invoice, and guessing a distance or a vehicle number would be forging the part
a checkpoint actually reads.

### Commerce packaging — DONE

The four packaging shapes the market research flagged, over the commerce machinery already
merged. Written up in full in [commerce-packaging.md](commerce-packaging.md); the short version is
that **three of the four were configuration**, which is the payoff of immutable plan versions and
an append-only ledger rather than a coincidence.

- **Graduated bundle discounts — configuration.** A bundle is an ordinary immutable plan version
  whose `features` declare `bundled_products` and `bundle_tier`; its price sheet *is* the bundled
  price. Not a discount flag over the standalone plans, so a bundled invoice stays reconstructable
  from the `plan_version_id` its line already records. The seeded ladder is 58,200 / 80,700 /
  95,600 paise per seat for 2, 3 and 4 products — 29,100 → 26,900 → 23,900 **per product**, which
  is what graduated means — and `domain.ladder_is_graduated` proves the monotonicity by
  cross-multiplying rather than dividing, because a rounded comparison of two prices is a
  comparison of two other prices. The test reads the seed migration's own numbers, so re-pricing a
  rung into a dearer-per-product ladder is a red build.
- **Prepaid credit packs — configuration, plus one correction.** A pack is a time-boxed credit
  grant and each invoice draws against it. What needed code was `drawn_from`: every consumption
  used to be windowed `[now, NULL)`, which outlives a time-boxed pack — the instant the pack
  lapses the grant leaves the balance `SUM` while the consumption keeps counting, and the balance
  reads *minus what was spent* for ever, eating unrelated credit. Exactly the defect the reversal
  path was fixed for, from the other direction, and it takes the same answer: name the pack and
  inherit its window. Remaining is a `SUM` over the grant and its draws, never a column.
- **A free view-only seat class — the one that needed a table.** `catalog.seat_classes` on the
  plan version: a `code`, a nullable `price_id` (NULL is free — the absence of a price row, never
  a zero-amount one), `included_count`, and `can_write`. Frozen with its version by the same
  trigger that freezes prices, because what a seat may do is a term of the deal.
  `domain.seat_class_grants` *computes* the permission set from the class: a view-only seat gets a
  whitelist of read actions with dangerous permissions excluded, so no write survives, a PII
  unmask does not survive either, and an unclassified verb (`approve`, `export`) is excluded by
  default rather than falling through.
- **Partner / reseller pricing — configuration, no code at all.** `restricted_to_org_id` pointed
  at the reseller: the same audience-of-one mechanism ADR-0004 §2 built for a negotiated
  enterprise price, inheriting the same `_audience_ok` filter and the same negative-authz test.
  Channel distribution was the same problem wearing a hat.

**Also true, and deliberately out of scope:** a bundle prices the packaging, it does not yet fan
entitlement out to its members. A subscription entitles one `product_key`. Lighting up all three
products of a `suite/trio` means the product-service projection reading `bundled_products` off the
subscription event and upserting one activation per member — real code in the entitlement path,
across three services, which is not a packaging slice. `bundled_products` ships now so that work
has a contract to read rather than a convention to guess.

**Next here:** the bundle entitlement fan-out above; per-seat-class quantities on
`subscription_items` (the pin already carries the classes, nothing counts seats by class yet); and
a customer-facing read of an organization's own prepaid packs, which today is an internal route a
billing run drives.

### HRMS employee-service — slice 1 landed; follow-ups

The Employee Master (`employee-service`, schema `employee`, port 8120) is in as one green
vertical slice: the `employees` table, the `probation → active → on_notice → exited` lifecycle
state machine, the `hrms.people.employee.read/write` permissions with `HR_ADMIN` and `EMPLOYEE`
role templates, scoped reads (OWN/TEAM/LOCATION/ORG), audit + outbox on every write, RLS on the
new schema, and the full negative-authz / cross-tenant test set. Two things are deliberately
left:

- **Sensitive PII (slice 2): DONE in #19** (`EmployeePii`, encrypted and masked). Original note: Date of birth, personal contact, PAN/Aadhaar/UAN/
  ESI, bank details and documents are *not* stored yet — "PII is encrypted and masked by
  default" is load-bearing and platform-core has no PII envelope helper. Slice 2 lands the
  envelope (encryption at rest, masking, `record_sensitive_read` on every unmask) and then adds
  those fields. Do not add any of them before the envelope exists.
- **Register the `hrms` product + role templates with account/product service: DONE** —
  closed as a platform mechanism, not a per-product edit (ADR-0005). Every product's
  `app/catalogue.py` is exported to a committed JSON manifest in `libs/contracts/permissions/`
  (`tools/export_permissions.py`, checked by the `openapi-check` gate row; the `hrms` manifest
  merges the employee and attendance slices), and account-service's consumer installs the
  activated product's manifest on `core.product.activated` / entitling
  `commerce.subscription.*` — permissions join the catalogue, role templates become org-owned
  system roles, and the admin-most template is granted to the org's owners.
  `tools/wire_product.py` drives the same install by hand for an already-activated org.
  Still open from the checklist: **declare plan features** (step 5) — that remains
  product-service seed data.

### End-to-end journey suite — DONE
`make e2e` boots six services (account, product, auth, customer, deal, app-gateway), an outbox
relay and an event consumer as subprocesses on ports the OS allocates, and drives one ordered
journey over real HTTP against real Postgres and RabbitMQ. Nothing is stubbed. ~20s.

The journey: the owner signs in → an administrator is refused activation → the owner activates
CRM → **the relay publishes the event and account-service consumes it** → the manifest installs
→ **every admin role the product ships is granted** → an entitled endpoint returns seeded rows →
the second service answers too → an unentitled product is 402 → another tenant's row is 404 →
every service resolves grants through the current internal-token scheme.

Written against the three defects that reached `main` on 2026-08-25, each invisible to unit
tests because each side stubbed the other:

- **(a) the silent relay stall** — steps 4-5 assert the outbox row reaches `published_at` and
  account-service's `processed_events`. The harness also runs every process with
  `ENVIRONMENT=ci`, which turns `assert_outbox_role`/`assert_app_role` from a warning into a
  startup failure, so a relay handed an RLS-bound role now dies at boot with the reason.
- **(b) version skew** — step 13 asserts every service in the suite answers 200 to a request
  the gateway signed. A service still verifying the old static shared secret cannot.
- **(c) a product spanning services** — CRM ships three admin templates (`CRM_BOOK_ADMIN`,
  `CRM_PIPELINE_ADMIN` and `CRM_LEADS_ADMIN`); the suite boots two of its three services, so step 7
  asserts the owner holds the two it can see (`e2e/test_journey.py:44`) and step 10 proves it with
  data from the second service. `lead` is not booted by the harness.

Three things the harness itself is careful about, each having already bitten: ports are
allocated rather than fixed (fixed ports made the suite silently drive a stack that was already
running); `DATABASE_URL`, the broker vhost and the Redis db are pinned by the harness and passed
explicitly, so no developer's `.env` can steer it into the shared database; and the gateway is
addressed as `localhost`, because an IP host makes it infer a tenant slug from the Host header.

**Still open:** the suite seeds its owner by setting `Membership.is_owner` directly, because
`add_member` cannot create one and there is no HTTP route for organization creation — the same
gap `tools/demo/promote_owner.py` exists to repair. And `_seed_product.py` promotes `crm` from
`soon` to `available` in the test database, since it is the only multi-service product and
activation rightly refuses a product that has not shipped.

### `product-service` — DONE (runs on the edge)
Product registry, per-org activation, entitlements, marketplace catalogue. Turns the gateway's
entitlement gate from *a claim the token happens to carry* into something backed by a registry.

Start emitting usage events here with `is_billable=false`. Turning on usage-based pricing later
then becomes plan configuration rather than an architecture change, and it costs almost nothing
now.

### `config-service` — scaffold only
Per-org settings, integration secrets, feature toggles.

---

## After that

| Slice | Why it is where it is |
|---|---|
| Platform services — document, notification, audit, scheduler, eventbridge | Every later product gets files, email, audit and cron without building them |
| **Event consumers** | The outbox produces and nothing consumes. Traced flows stay theoretical until handlers exist |
| HRMS rebuild — employee, attendance, leave, payroll, recruit | Fix the known timezone defect **during** the rebuild rather than porting it |
| PulseBoard rebuild + data migration | **Riskiest slice** — live customer data, preserved UUIDs. Needs a *rehearsed* rollback, not a written one |
| CRM greenfield — customer, sales, marketing, support | CRM owns Customer/Contact; PulseBoard's clients migrate onto it |
| Billing — catalog, subscription, metering, billing, payment | **Razorpay adapter first** |
| Ecosystem surface — super-admin console, marketplace, workflow, analytics | |

**Why Razorpay before Stripe.** It is the more constrained provider: mandate caps, a legally
required pre-debit notice, multi-day eMandate activation, no true idempotency key. An interface
designed Stripe-first produces abstractions Razorpay cannot satisfy — discovered after it has
ten call sites.

---

## Known gaps not yet scheduled

- **Internal credentials are now short-TTL signed tokens on both channels** — the
  `/internal/v1` calls mint per-callee tokens, and the gateway's identity-forwarding hop mints
  a per-request token bound to the forwarded user and organization
  (`platform_core.internal_auth`); the raw secret no longer travels and production refuses the
  `dev-only-`/`ci-only-` placeholders. Still open: the signing key is one shared HMAC secret
  (end state is per-service keypairs or mTLS), and no network policy makes `/internal/v1`
  unreachable from ingress.
- **`record_sensitive_read` is wired in**: the employee PII unmask writes an
  `entity.sensitive_read` row in the caller's transaction (tested in
  `services/employee/tests/test_pii.py`), and the account directory's cross-tenant reads leave
  a structured-log trace (`directory.cross_tenant_read`) — an `audit_logs` row is unwritable
  there, since the read predates any organization and the caller is a service, not an actor.
- **Outbox-lag probe exists (`/health/outbox`); alerting on it does not.** A stalled relay is visible only to whoever looks. Worth adding before RLS lands, so there is
  a baseline to compare against.
- **SAML is not implemented, and neither is just-in-time provisioning.** OIDC now flows end to
  end (see below), and `org_idp_configs` carries the SAML columns it always did —
  `idp_entity_id`, `idp_sso_url`, `idp_x509_cert` — with nothing reading them. Deferred on
  purpose rather than half-built: SAML is a different trust model (signed XML assertions, XML
  canonicalisation and signature-wrapping attacks, IdP-initiated flows with no request to bind
  a response to) and shares almost nothing with the OIDC path but the configuration row. It is
  worth doing when a customer's IdP cannot speak OIDC; Google Workspace and Entra — which is
  most of the Indian mid-market — can.

  `jit_provisioning` on the same table is likewise inert and documented as such in the model. An
  unknown address is refused, because creating a user, a membership and role grants is
  account-service's job and this platform has exactly one door into the directory: an
  invitation. Opening a second one where an external issuer decides who joins and a column
  decides their roles is a bigger decision than a sign-in flow, and it should be made on its
  own.
- **10 of the 18 quality-gate rows have no tooling** — production build, E2E, schema drift,
  visual regression, automated accessibility, performance budget, query plans, SAST and licence,
  dependency boundaries, token lint.
- **`opsgrids-internal` is not on the shared packages yet.** `opsgrids-ui` and `opsgrids-client` now
  hold the design system and the auth/HTTP layer, and all nine `product-*` apps consume them;
  the staff console still carries its own copies. It is a real fork rather than an oversight —
  its palette is a different token family (lifecycle statuses, a monospaced control-panel face)
  and its auth is the staff registry from ADR-0004 P6a, which has no organization selection. It
  joins once that endpoint lands and the operator palette is expressed as a theme over the
  Console tokens rather than a replacement for them.
- **The Engage send gate answers in prose, and the UI matches on the prose.**
  `POST /engage/campaigns/{id}/send` refuses with a `422` whose only content is one sentence
  (`services.request_send`, `_check_public_base`); `frontend/product-engage/src/lib/sendGate.ts`
  chooses the next step it offers ("Open Senders", "Add a subject") by matching a lower-cased
  substring of it. Nine sentences, nine substrings, held together by
  `services/notification/tests/test_engage_send_gate_sentences.py` — which drives every gate
  through the real route and reads the substrings out of `sendGate.ts`, so a reword on either
  side fails a test. That is a guard, not a fix. **The fix is a stable machine `code` on the
  `422` problem document** (`unusable_sender`, `domain_unverified`, `no_connection`,
  `no_subject`, `no_body`, `list_gone`, `sender_gone`, `unsubscribe_unconfigured`,
  `public_base_unset`) that the UI switches on, after which the prose is free to change and both
  the substring match and its test can be deleted. It needs an additive field on
  `BusinessRuleProblem` in `libs/platform-core` (the `remedy` extension on the inbox reply `409` is
  the precedent) and a contract entry in `libs/contracts`.

---

## Branches deliberately not merged

`feat/tenant-chokepoint`, `feat/e2e-journey` and `worktree-feat+tenancy-prerequisites` are
parallel implementations of work `main` already carries. Their tests fail here not because the
behaviour differs but because they are written against a different internal design — a
`tenant_transaction` helper where this has `get_tenant_session`, per-service DSN environment
variables where this rewrites a role name into one URL.

Adopting them would mean adopting that design: a rewrite of working, tested code to arrive at
the same place. The properties they assert are already asserted here against the design that
exists. The useful pieces were taken instead — the audit coercion, the gate tooling, the outbox
role, the invitation-accept fix, and the cross-tenant sweep and query-plan checks.

They are left on the remote rather than deleted, since deleting somebody else's branch is not
this session's call to make.

## Delivered

| Slice | Commit |
|---|---|
| Platform foundation — `platform-core`, dev bootstrap, CI, service generator | `659ea5b`, `90ed777` |
| Marketing website + frontend workspace | `c788ab0` |
| account-service — orgs, units, locations, users, memberships, roles, invitations | `4210187`, `cf858eb` |
| auth-service — credentials, sessions, refresh rotation, MFA, SSO config, keys | `a6e3548` |
| Gateways — `app-gateway` and `admin-gateway` | `ca58de3` |
| Internal API + permission-version bump | `15d6ec0` |
| Single sign-on — OIDC authorization code with PKCE, tenant login | `<pending>` |

Two of those closed defects that would have been outages in production: the gateways shipped
with a **duplicated correlation header** and an **unenforced per-route `staff_only`** flag, and
account-service was **serving none of the internal endpoints** that auth and both gateways call.

### Single sign-on, in one paragraph

`GET /api/v1/auth/sso/start?email=` reduces the address to its domain, asks account-service
which organization **DNS-verified** that domain (`GET /internal/v1/domains/{domain}`), binds the
transaction to that organization, and reads its OIDC configuration — which is the one auth
table under row-level security, so the binding is what makes it readable at all. It returns the
provider's authorization URL carrying `state`, a `nonce` and an S256 PKCE challenge; the state,
nonce and code verifier live in Redis for ten minutes, single-use.
`GET /api/v1/auth/sso/callback` consumes the state atomically (a replay finds nothing and never
reaches the provider), exchanges the code with the verifier, verifies the ID token's signature
against the provider's JWKS along with issuer, audience, expiry, nonce and `email_verified`,
maps the address to a member of that organization, and mints through the **same** `issue_for`
every other sign-in uses — same audience, same `pv`, same claim shape, `amr: ["sso"]` instead of
`["pwd"]`. Per ADR-0000 §1 the domain selects a *provider*, never an organization: the
organization comes from the configuration that provider belongs to, and membership is still
required. The client secret is sealed with the platform PII envelope, never returned by any
endpoint and never logged. Unknown addresses are refused — see the note above on JIT
provisioning. Configuration is `GET`/`PUT /api/v1/auth/sso/config`, behind
`core.identity.sso.read` / `core.identity.sso.write` at ORG scope.
