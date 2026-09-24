# Finance service

The accounts-receivable module of the OpsGrids platform, and the home of its GST compliance engine.
Owns the `finance` schema: customers, invoices and credit notes, invoice lines, payments and their
allocations, the GST rate schedule, the organization's GST registration, the e-invoice filing
ledger, the purchase book, the IMS reconciliation queue, the GSTR-1 return record, the e-Way bill
ledger and the TDS/TCS withholding register. Serves the `product-finance` SPA.

```bash
make run SERVICE=finance PORT=8171    # API
make worker SERVICE=finance           # event consumer — auto-files finalized documents
make relay SERVICE=finance            # outbox relay
```

## Scope

The finance SPA is read-only today (no create affordances), so most of the HTTP surface is the
reads it renders. The invoice and payment write path
(`services.create_customer/create_invoice/record_payment`) exists to finalize the append-only
documents and to drive the demo seed through the real, RLS-bound path.

Compliance is where the writes are. Three are automatic — filing an invoice, syncing a supplier
ledger, and assessing what will be withheld on a document as it is finalized — one is
service-to-service (raising an e-Way bill for a consignment a dispatch system is about to move),
and three need a person: setting the organization's GST registration, deciding an inbound supplier
document, and generating and filing a return.

## Owns

- **`customers`** — the billed party (name, contact, GSTIN, city/state).
- **`invoices`** — a tax invoice or a credit note. Frozen at finalize; totals foot exactly
  (`subtotal = Σ line.amount`, `total = subtotal + tax`). A correction is a new `credit_note`
  document carrying `corrects_invoice_id`, never an edit. `amount_paid` is **derived** from the
  payment allocations, never stored, so the header cannot drift from the cash applied to it.
  Carries its own GST facts: place of supply, supply type, reverse charge, both GSTINs as
  snapshots, and the CGST/SGST/IGST split.
- **`invoice_lines`** — append-only; foot to the invoice subtotal; may be negative on a credit
  note. Each carries its HSN/SAC, its own rate, and its own share of the split.
- **`payments`** / **`payment_allocations`** — append-only; a payment splits across the invoices
  it settles.
- **`gst_registrations`** — the organization's own GSTIN, per state, one primary. Configuration,
  so mutable; what must not change is the GSTIN a filed invoice was filed under, which is a
  snapshot on the invoice rather than a join to here.
- **`einvoice_filings`** — one row per attempt to register a document with the IRP. **Append-only
  at the database**, by trigger.
- **`purchase_documents`** — what suppliers billed this organization; the counterpart an IMS
  document is matched against. Tax computed by `domain.split_gst`, never supplied.
- **`ims_documents`** — what suppliers *filed* against this organization, keyed on the portal's own
  row reference so a re-sync creates nothing and a genuine duplicate stays representable.
- **`ims_reconciliations`** — the computed suggestion per document, appended per run. Derived
  opinion, deliberately **not** frozen.
- **`ims_decisions`** — who accepted or rejected what, and when. **Append-only at the database**,
  by trigger, because this is the row that decides whether a credit is claimed.
- **`ims_decision_pushes`** — whether GSTN has been told what one decision says: one row per
  decision, a state machine (`queued → submitting → pushed | failed | conflict`, or `superseded`)
  with a lease and a retry budget. **Not** append-only, deliberately — a trigger freezes its
  identity, forbids deletes and allows only the legal status edges, and every transition writes an
  audit row. The decision it carries is never touched (ADR-0015, C2).
- **`gst_returns`** — one row per (registration, period, type). Carries the **snapshot** of what
  was assembled, as JSONB, plus the one-line totals denormalised out of it. Frozen by a trigger
  once **filed**; before that it may be re-assembled, because nothing has been reported yet.
- **`gst_return_transitions`** — the append-only status trail the `status` column is only the head
  of. Who moved a return to `filed`, when, with which ARN, and what the totals were at the time.
- **`eway_bills`** — one row per attempt to license a goods movement. **Append-only at the
  database**, by trigger, like `einvoice_filings`. Stores the distance and `valid_from` as well as
  `valid_until`, so the deadline stays recomputable rather than merely remembered.
- **`invoice_withholdings`** — TDS deducted and TCS collected, per document per head.
  **Append-only at the database**, by trigger. The rate and threshold are snapshots beside the id
  of the schedule version that produced them.
- **`tax_rates`** and **`withholding_rates`** — the GST rate schedule and the statutory
  withholding schedule, both versioned by `effective_from`. **Global / UNPOLICIED** (no
  `organization_id`): a rate band and a 2 % deduction under s. 51 are platform vocabulary, the
  same for every tenant in India, not tenant data. Left out of row-level security alongside
  `processed_events`.

Money is integer minor units (paise) throughout; there are no floats and no `Numeric(_, 2)`.

## How a document gets filed

1. `create_invoice` finalizes a reportable document and stages
   `finance.compliance.invoice.filing_requested` **on the outbox, in the same transaction**. The
   relay sends it only after the invoice has committed.
2. `app/consumers.py` picks it up, binds the tenant GUC for the message, and calls
   `services.autofile_invoice`.
3. That asks `live_registration` **before** touching the portal — a document already carrying an
   IRN is never sent again — then files through `IrpClient` and appends exactly one attempt row.
4. Success persists the IRN, acknowledgement number and date, and the signed QR verbatim, and
   publishes `finance.compliance.invoice.filed`.
5. A rejection is terminal at once. An outage is retried on a capped exponential backoff up to
   `domain.MAX_FILING_ATTEMPTS`; when the budget is spent it becomes a terminal `failed` row
   carrying `retry_exhausted`, and the invoice read model reports `terminal: true`.

A registered IRN may be withdrawn within 24 hours of its acknowledgement. Past that the only
remedy is a credit note, and the read model says which of the two is available (`remedy`) and until
when (`cancellable_until`) rather than offering both.

## How a supplier document gets reconciled

`POST /internal/v1/ims/sync` fetches a return period through `ImsClient`, stores what is new
(idempotent on the portal's reference), then re-runs the reconciliation. Each document gets a
suggestion **with its reason** — `matched`, `amount_mismatch` (with the delta, signed, in paise),
`supplier_unknown`, `no_purchase_record`, `duplicate`. Nothing is auto-accepted: the status stays
`pending` until somebody records a decision, because accepting claims the input tax credit on
somebody else's invoice.

**Deciding and telling GSTN are two acts** (ADR-0015, C2). `POST /finance/ims/documents/{id}/decisions`
(and `POST /finance/ims/decisions` for up to 100 at once, all or none, each with its own reason)
appends to `ims_decisions` and queues a push in the same transaction; nothing reaches the portal
until a person holding `finance.compliance.ims.push` asks for a period to be sent
(`POST /finance/ims/pushes`, **202**). A worker then runs claim / call / record per batch, one
period at a time, each item under the key `(period, portal reference, decision version)`, so a crash
between the call and the record is repaired by asking again — "already recorded" is success. A
refusal is `failed`, a disagreement with what GSTN holds is `conflict` (never retried, never pushed
over), an outage retries on a 1 to 30 minute backoff for six attempts, and the reclaim sweep in
`finance-worker` finishes what a crash left. The next sync compares what the portal shows with what
was pushed and flags `drift`; the platform never "fixes" it. Input tax credit follows the newest
*decision*, not the push: only an accept claims it, and leaving accepted takes it back out as a new
ledger row, so GSTR-3B 4A never contains a rejected or held document.

## How a return gets assembled, and why it is frozen

`POST /finance/gst/returns` assembles one period's outward supplies from the invoices already
recorded and stores the result as a **snapshot**.

Nothing in that assembly computes tax. `domain.assemble_gstr1` sums the splits the invoices
already carry, so the return foots to the documents it is made of *by construction* rather than by
a reconciliation step somebody has to remember to run. Every reported document lands in exactly
one section — EXP, then CDNR/CDNUR, then B2B, then B2CL, then B2CS — which is what makes the
footing provable: a document that could fall into two sections would double a figure, and one that
fell into none would lose it.

The sections are the real ones. B2B and B2CL invoice-wise; **B2CS aggregated** by place of supply,
rate and inter/intra-state, because that is what the return declares; CDNR/CDNUR carrying the
number each note corrects; EXP; an **HSN summary** built in parallel from the same lines rather
than from the section entries (two independent declarations of one book, so a test comparing them
is a real cross-foot and not a tautology); and a **document series** — the one section a *void*
belongs in, since its number was consumed even though its figures are not a supply.

Once the return is **filed**, a trigger freezes the snapshot, the totals, the period and even
`deleted_at`. A correction that arrives afterwards therefore shows up through
`GET /finance/gst/returns/{id}/diff` as a **difference**, per section and in total — which is what
an amendment in a later period declares — rather than by August quietly becoming a different
return. Before filing it may be re-assembled, and every assembly appends its own transition row.

## How an e-Way bill gets raised, and when it dies

`POST /internal/v1/eway/bills` licenses a goods movement above ₹50,000. The transport facts — a
distance, a vehicle, a transporter — come from the caller, because they are not on an invoice;
everything else comes off the frozen document.

**Validity is the platform's arithmetic, not the portal's.** `EwayAck` deliberately carries no
deadline: one day of validity per 200 km *or part thereof*, each day expiring at midnight of the
day immediately following generation — so a bill raised at 23:50 for 150 km is valid for ten
minutes, not for a day. `domain.eway_valid_until` computes it from the `distance_km` and
`valid_from` the row **stores**, so a screen and a roadside checkpoint are reading the same
arithmetic over the same two values. A deadline only the portal knows is one nothing can explain
and no test can check, and the cost of the two disagreeing falls on a driver.

Because `eway_bills` is append-only, nothing can update a row when the clock passes it: `expired`
is derived on read (`effective_status`) rather than written. A bill may be cancelled within 24
hours of being raised, checked against the stored `valid_from`; past that, and once it has
expired, there is no remedy at all — the movement has to be reported as it happened.

**An outage is retried; a refusal is not** (ADR-0015, C4). A timeout, a 5xx or a rate limit
(`EwayUnavailable`, `EwayRateLimited`) leaves a `pending` row naming its `attempt` and `retry_at`,
and the next attempt is scheduled in the same transaction (`schedule_event`, backoff
`min(1 min × 2^(n−1), 30 min)`, eight attempts). A portal refusal (`EwayRejected`) is a terminal
`failed` row carrying its code and its masked words, with a remedy the screen states. Every attempt
of a chain sends the idempotency key its first claim computed, so a retry can never mint a second
bill; a chain first asked for more than 24 hours ago ends `failed` `stale` without a call. The
reclaim sweep is the net under the schedule. `POST /finance/eway-bills/retries` (**202**, permission
`finance.compliance.eway.retry`) is a person's "retry now": it stages the request and makes no
portal call.

## How tax gets withheld

Three heads, two statutes, and they work differently in ways that get conflated:

- **GST s. 51** — the recipient deducts **2 % of the taxable value** on notified supplies (metal
  scrap is the case this vertical meets) above ₹2,50,000 under one contract. A **cliff**: below
  the threshold nothing, above it the rate on the *whole* value. It splits across CGST/SGST/IGST
  by reusing `domain.split_gst`, so a deduction cannot round differently from the levy beside it.
- **Income tax s. 194Q** — the buyer deducts 0.1 % of purchases from one seller beyond ₹50 lakh in
  a financial year. An **aggregate**: the rate bites only on the excess, so the invoice that
  crosses the threshold is withheld on partially.
- **Income tax s. 206C(1H)** — the seller collects the same 0.1 % beyond the same ₹50 lakh, and
  **must not** where the buyer is already liable under 194Q. Mutually exclusive by statute.

Assessment runs inline when a document is finalized — it is arithmetic over rows already in the
transaction, unlike filing, which calls a government portal and therefore goes through the outbox.
It is **gated on three flags on the customer**, all defaulting false, because whether a body is
notified under s. 51 and whether a buyer is liable under 194Q are facts about somebody else's
registration and somebody else's books. The platform cannot infer them, so it does not guess.

`withholding_rates` is versioned by `effective_from` and resolution is against the **document's**
date, with the resolved rate and threshold snapshotted onto the register row beside the id of the
version they came from. Publishing tomorrow's rate today therefore changes nothing that was
already computed — which is what "a rate change must not restate history" means, and there is a
test that publishes one and re-derives an old invoice to prove it.

`GET /finance/gst/withholdings/summary?period=` groups the register **per counterparty**, which is
how GSTR-7 is declared — the shape that makes these figures comparable to the return a deductor
actually files, and therefore reconcilable.

## Endpoints (`/api/v1/finance/...`)

| Method & path | Returns | Permission |
| --- | --- | --- |
| `GET /finance/invoices?q=&status=&sort=` | `Page<Invoice>` | `finance.billing.invoice.read` |
| `GET /finance/invoices/{id}` | `Invoice` | `finance.billing.invoice.read` |
| `GET /finance/invoices/{id}/filings` | `EInvoiceFiling[]` | `finance.compliance.einvoice.read` |
| `GET /finance/gst/registration` | `GstRegistration` | `finance.compliance.registration.read` |
| `PUT /finance/gst/registration` | `GstRegistration` | `finance.compliance.registration.write` |
| `GET /finance/ims/documents?q=&status=&supplier_gstin=&period=&reason=&push_status=&drift=&sort=` | `Page<ImsDocument>` | `finance.compliance.ims.read` |
| `GET /finance/ims/documents/{id}` | `ImsDocumentDetail` | `finance.compliance.ims.read` |
| `POST /finance/ims/documents/{id}/decisions` | `ImsDecision` | `finance.compliance.ims.decide` |
| `POST /finance/ims/decisions` (up to 100, all or none) | `ImsBulkDecision` | `finance.compliance.ims.decide` |
| `GET /finance/ims/pushes?period=&status=&drift=` | `Page<ImsPushWithDocument>` | `finance.compliance.ims.read` |
| `GET /finance/ims/pushes/summary?period=` | `ImsPushSummary` | `finance.compliance.ims.read` |
| `POST /finance/ims/pushes` (**202**) | `ImsPushRequest` | `finance.compliance.ims.push` |
| `GET /finance/gst/returns?period=&status=&return_type=&sort=` | `Page<GstReturn>` | `finance.compliance.returns.read` |
| `GET /finance/gst/returns/{id}` | `GstReturnDetail` | `finance.compliance.returns.read` |
| `GET /finance/gst/returns/{id}/diff` | `ReturnDiff` | `finance.compliance.returns.read` |
| `POST /finance/gst/returns` | `GstReturn` | `finance.compliance.returns.generate` |
| `POST /finance/gst/returns/{id}/filing` | `GstReturn` | `finance.compliance.returns.file` |
| `GET /finance/eway-bills?status=&invoice_id=` | `Page<EwayBillWithInvoice>` | `finance.compliance.eway.read` |
| `GET /finance/invoices/{id}/eway-bills` | `EwayBill[]` | `finance.compliance.eway.read` |
| `GET /finance/invoices/{id}/eway` | `EwayState` | `finance.compliance.eway.read` |
| `POST /finance/eway-bills/retries` (**202**) | `EwayState` | `finance.compliance.eway.retry` |
| `GET /finance/gst/withholdings?period=&code=&invoice_id=` | `Page<WithholdingWithInvoice>` | `finance.compliance.withholding.read` |
| `GET /finance/gst/withholdings/summary?period=&code=` | `WithholdingSummary` | `finance.compliance.withholding.read` |
| `GET /finance/gst/withholding-rates` | `WithholdingRate[]` | `finance.compliance.withholding.read` |
| `GET /finance/payments` | `Page<Payment>` | `finance.billing.payment.read` |
| `GET /finance/customers?q=&sort=` | `Page<CustomerSummary>` | `finance.billing.customer.read` |
| `GET /finance/overview` | `OverviewSummary` | `finance.reports.summary.read` |
| `GET /finance/ledger` | `LedgerReport` | `finance.reports.ledger.read` |

Internal (service token, excluded from the published contract):
`POST /internal/v1/einvoice/filings`, `POST /internal/v1/einvoice/filings:cancel`,
`POST /internal/v1/ims/sync`, `POST /internal/v1/eway/bills`,
`POST /internal/v1/eway/bills:cancel`.

`sort` for invoices: `issued_desc|issued_asc|amount_desc|amount_asc|due_asc`; for customers:
`receivables_desc|overdue_desc|name_asc`; for the IMS queue:
`date_asc|date_desc|amount_desc|amount_asc` (oldest first by default — a queue is worked forward).
The KPI spark-lines/deltas, six-month cashflow, cash balance and trial-balance accounts are
representative booked figures (see `app/domain.py`) that mirror the SPA fixtures — the seam a real
cash-ledger / GL integration replaces; the revenue, receivables, overdue and AR-aging figures are
derived live from the invoices and payments.

## Permissions and roles (`finance` product)

Finance is an **organization-level** function — a company's books have no per-user "own" reading —
so every permission defaults to ORG, tops out at ORG, and the routes floor at ORG.

- `finance.billing.invoice.read`, `finance.billing.payment.read`, `finance.billing.customer.read`
- `finance.reports.summary.read`, `finance.reports.ledger.read`
- `finance.compliance.einvoice.read`, `finance.compliance.registration.read`,
  `finance.compliance.ims.read`, `finance.compliance.returns.read`,
  `finance.compliance.eway.read`, `finance.compliance.withholding.read`
- `finance.compliance.registration.write`, `finance.compliance.ims.decide`,
  `finance.compliance.ims.push` and `finance.compliance.returns.file` — the four **dangerous**
  writes, because each decides money or speaks to a government: which identity every future
  invoice is filed under, whether the credit on a supplier's invoice is claimed, whether GSTN is
  told what was decided, and what a tax period is declared to have contained. Recording a decision
  and sending it are separate permissions on purpose: a clerk may record decisions for a
  supervisor to review without being able to send them.
- `finance.compliance.eway.retry` — a write that is deliberately *not* dangerous: it replays
  transport facts the dispatch system already asserted, under the key that makes a second bill
  impossible. Both new permissions reach existing organizations only after the ADR-0007 reconcile.
- `finance.compliance.returns.generate` — a write, but deliberately *not* dangerous and
  deliberately separate from `.file`. Generating states what this platform believes about its own
  book and can be repeated; filing is terminal and leaves the building. A finance analyst can hold
  the first without the second, and a test asserts neither implies the other.

Role templates: **FINANCE_ADMIN** (the whole catalogue at ORG) and **FINANCE_VIEWER** (default;
every read — the supplier queue, the returns, the e-Way ledger and the withholding register — and
none of the writes).

## The three stubbed portals

`IrpClient`, `ImsClient` and `EwayClient` are `Protocol`s; `StubIrpClient`, `StubImsClient` and
`StubEwayClient` are the implementations this repository ships. All three are real everywhere
except the wire — the IRN is the government's own hash, the refusals carry the portals' numbered
codes, and every fixture's tax comes from `domain.split_gst` — and none opens a socket or reads a
credential, which a test asserts. Selecting a real NIC or GSP client is a wiring change in
`app/deps.py`.

Three clients rather than one, because they are three products with separate credentials and
separate downtime: a business can be perfectly current on e-invoicing while its e-Way integration
is unreachable, and a single client would make that one outage.

The e-Way stub has one honest deviation, called out in its docstring rather than left to be
discovered: the real portal issues bill numbers from a sequence, and this one derives a
twelve-digit number from the document **and the moment it was raised**. Deterministic in its
inputs, but deliberately not one number per document — a document legitimately carries several
bills over its life (cancelled and re-raised, or expired in transit while the goods still have to
arrive), and a number keyed on the document alone would collide on the unique index the second
time and strand a lorry because a hash said so.

## Demo seed

`tools/demo/seed_finance.py` mirrors the SPA fixtures exactly (12 customers, 21 documents, 9
payments) into the "OpsGrids Demo" org via the real write path, so the live screens match the
approved fixtures. Idempotent. `DATABASE_URL` must point at `opsgrids_wt`.
