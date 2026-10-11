# ADR-0021 — Consent: an append-only ledger and one decision every send path asks

Status: **accepted (built)** · Date: 2026-09-26 · Service: `notification` (both products, `notification` and `engage`)

Builds on [ADR-0000](0000-phase-0-decisions.md) §9 (append-only is a table privilege) and §10 (a
schema per service, RLS, no cross-schema foreign keys); [ADR-0002](0002-row-level-security.md)
(policies, the transaction-local tenant GUC); the Engage design spec
(`docs/superpowers/specs/2026-09-21-engage-email-platform-design.md`, "Slice 2": a `consent_event`
that is append-only, "a history with source, timestamp and IP, never a boolean that gets
flipped"); and `docs/whatsapp/audit.md` §11 (marketing WhatsApp is "a consent regime": opt-in
recorded "with provenance — who consented, when, to what"). No numbered messaging ADR existed, so
this is the first.

---

## Context

Consent was split across two stores that never looked at each other:

- `preferences` holds **opt-outs** for rule-driven sends ("absence means send") and is checked when
  a message is staged and again when it is claimed for dispatch.
- `suppressions` (email, blind-indexed) is checked **only** by campaign recipient selection
  (`selectors.sendable_contacts`) and the per-batch claim.

The two are disjoint, nothing cross-checks them, and neither records **who** opted in, **when**, or
through **what** source. `preferences` cannot express an opt-*in* at all, and Meta requires one for
business-initiated WhatsApp messages. `docs/whatsapp/audit.md` §4 refused a second WhatsApp opt-out
table because "a second source of truth for consent … is the one place that must never disagree";
this ADR is the third-store problem answered the other way: one record, one decision, and the two
older stores made to agree with it rather than replaced under live tenants.

## Decision

**1. `consent_events`: an append-only ledger.** One row per fact: `organization_id`, `channel`
(`whatsapp | email | sms`), `purpose` (`marketing | transactional`), the **subject** (the address,
sealed, plus the keyed blind index, plus a mask), optional `contact_id` (a plain UUID, no foreign
key), `status` (`opted_in | opted_out`), `source`, `evidence`, `recorded_by`, `occurred_at`,
`created_at`, and an optional `idempotency_key` (unique per organization when present).

- **Append-only is enforced by the database.** `UPDATE` and `DELETE` are revoked from the app role
  in the migration and re-applied by `migrations/env.py` after every `apply_schema_grants` (which
  re-grants both on every table), the same shape as the console's `revenue_events`. There is no
  `updated_at`, `deleted_at` or `TenantBase`: a column that implies a row can change invites the
  code that changes it. A change of mind is a new row.
- **No plaintext address anywhere.** `subject_enc` (sealed by `platform_core.pii`),
  `subject_masked` (what reads return) and `subject_index` (the HMAC blind index, *the same one*
  `contacts.email_index`, `suppressions.email_index` and `conversations.wa_address_index` carry, so
  nothing is decrypted to look one up and legacy rows are copied in without being opened).
- **RLS** as on every tenant table: `ENABLE` and `FORCE`, `USING` and `WITH CHECK` on
  `organization_id = current_org()`.
- `evidence` is a short reference, never the address. The API refuses evidence containing the
  subject's own address.

**2. `app/consent.py::decide(session, organization_id, channel, purpose, address)`**, returning
`ConsentDecision(allowed, reason)`. The latest event for `(organization, channel, purpose,
subject)` wins, ordered by `(occurred_at, created_at, id)`:

- **transactional**: allowed unless the latest event is `opted_out` (and, for email, unless a
  `bounced` or `complained` suppression exists — facts about the mailbox, not choices).
- **marketing**: allowed **only** if the latest event is `opted_in`. `opted_out` denies, and so
  does **no event at all**: silence is not consent.
- A STOP keyword opts the subject out of **both** purposes on that channel (two rows, one per
  purpose, so "latest per purpose" stays a single ordering). An email **unsubscribe link** opts the
  address out of **marketing only**: it is the link in a marketing mail, and invoices and receipts
  are not marketing (this is also what a legacy unsubscribed suppression always did).
- A rule is `marketing` if its `category` is `marketing` **or** the provider template it sends is
  registered `marketing`. Meta's classification carries the obligation; a rule's free-text bucket
  can neither add to it nor waive it. `in_app` has no consent regime.

**3. The legacy stores still count.** `decide` also refuses when a `Preference` or a
`Suppression` says no — **deny if either says no** — so nothing blocked before the ledger becomes
allowed by it. Refusals in either store are also written through to the ledger (STOP/START, the
unsubscribe link, `set_preference`, `add_suppression`, `remove_suppression`), so the ledger alone
tells the same story and the legacy check is a belt over braces rather than a second opinion that
could disagree.

Two corollaries, both deliberate:

- **Lifting a refusal restores consent that existed; it never creates it.** START, or an operator
  lifting a preference or a manual suppression, re-opens transactional and re-opens marketing only
  if the subject had opted in before. A bare START is not the affirmative, specific opt-in Meta
  requires.
- **Recording an opt-in does not lift a legacy block.** An `opted_in` for an address whose
  suppression is `unsubscribed`, or whose WhatsApp preference is a customer's STOP, is written (the
  person did say it) but the decision still refuses until the legacy row lifts. The API returns
  `effective` so the caller sees that.

**4. Call sites** (small edits; nothing was refactored): `stage_message` and `claim_for_send` call
`consent.check_send`, and a refusal sets `Message.suppression_reason` (`consent:no_opt_in`,
`consent:opted_out`, `consent:preference_suppressed`, `consent:suppressed_<reason>`).
`selectors.sendable_contacts` carries the SQL form of the same predicate (`latest_consent_is`),
because the audience is read a page at a time and a decision per contact would be a query per row;
**a test pins the two together across every state a subject can be in.** Because the send gate
(`_ensure_campaign_sendable`) and expansion both read `sendable_contacts`, an audience nobody in
which consented fails at the gate with a sentence rather than completing `sent` to nobody.
`claim_campaign_batch` asks again per batch (`consent.decide_indexes`, two queries however many
recipients) and records the reason on the recipient (`error = consent:opted_out`). The inbound
STOP/START handler and both unsubscribe POSTs (one-click and the confirm form) write the ledger;
the confirmation `GET` writes nothing.

**5. Backfill, in the migration** (`d5a7c3e91f48`, via `app/consent_backfill.py`, idempotent, and
re-runnable by hand as the admin role: `python -m app.consent_backfill`):

- every `Suppression` → `opted_out`, reason mapped to source (`unsubscribed` → `unsubscribe_link`,
  `manual` → `admin`, `bounced` → `bounce`, `complained` → `complaint`). Unsubscribed and manual
  opt out of **marketing only** (all they ever did was stop campaigns; rule-driven transactional
  mail to those addresses carried on and still does); a bounce or complaint opts out of both;
- every `Preference` refusal → `opted_out` for the purposes its category covers (`*` both,
  `marketing` marketing, anything else transactional), `inbound_keyword` if a customer's STOP,
  else `admin`; an operator's free-text reason is not copied (the column is not sealed);
- every active, unsuppressed contact with no marketing event → `opted_in` email/marketing,
  `source = legacy_import`, with evidence saying it predates the ledger. **This is unverified
  consent.** It exists so existing campaigns select the same recipients, and is not evidence of
  anything;
- **no WhatsApp equivalent**: there were no legacy WhatsApp marketing sends, so WhatsApp marketing
  needs an explicit `opted_in` from now on.

**6. Adding a contact is not an opt-in.** *(Amended 2026-10-12; this decision originally recorded
an operator's add, import or address edit as an `opted_in` "attestation … (unverified)".)* An
operator adding someone is not that person agreeing to marketing, and a consent law needs evidence
of the agreement. `create_contact` records an `opted_in` only when the operator supplies how the
person agreed and a reference to the record of it (`marketing_opt_in: {source, evidence,
occurred_at?}`, held to the same checks as `POST /consent/events`); `import_contacts` and an address
edit record nothing. A contact without an opt-in is in the audience but not mailable. Attestation
rows written before the amendment stay in the ledger as history (it is append-only), so contacts
added before it stay sendable; filter on `source = admin` / `import` with evidence containing
"attests" to find them.

**7. API** (under `/engage`, Engage entitlement, **no new permission codes**, so no production role
reconcile): `POST /consent/events` and `GET /consent/events` and `POST /consent/events:lookup`
ride `engage.audience.contact.write` / `.read`; `POST /consent/events/{id}:unmask` rides the
existing dangerous `engage.inbox.message.unmask` with its `amr=otp` step-up and writes an audit row
every time. A caller may claim only `checkout`, `web_form`, `import`, `admin`, `api`; the system
sources (`inbound_keyword`, `unsubscribe_link`, `bounce`, `complaint`, `legacy_import`) cannot be
forged, for the reason `SuppressionIn` has no `reason` field.

**WhatsApp opt-in capture (ENGAGE-02, 2026-10-12).** Two capture surfaces now write WhatsApp
marketing `opted_in`, so §5's "needs an explicit `opted_in`" is reachable without the raw API:

- The Engage contact view records one through `POST /consent/events` naming both the number and
  the `contact_id` (the link a WhatsApp campaign reaches the contact by), with the new API sources
  `in_person` and `phone_call` (or `web_form`). Withdrawing sends `opted_out` with the
  `contact_id` alone: the number on record for that contact is opted out, because staff only ever
  see it masked. `GET /consent/events?contact_id=…&channel=whatsapp` returns every event about any
  number linked to the contact, a STOP that named no contact included.
- The storefront enquiry form's unticked-by-default box. Storefront stamps
  `enquiries.whatsapp_opt_in_at` and stages `storefront.enquiry.whatsapp_opted_in` (ids only);
  notification's consumer reads the number from storefront's audited, notification-only
  `GET /internal/v1/enquiries/{id}/whatsapp-opt-in`, links or creates the Engage contact for the
  buyer's email (creating one attests **nothing** about email), and appends the event with the new
  system source `storefront_enquiry`, idempotent per enquiry.

## Why

- **One decision beats two stores that agree by convention.** A function every path calls cannot be
  bypassed by a new path forgetting to; two tables checked by different callers already were.
- **Append-only as a privilege**, not a habit, because the value of a consent record is that it
  cannot be edited after a complaint.
- **Latest-wins by `occurred_at`** so a back-dated opt-in cannot resurrect consent a newer refusal
  withdrew, and future-dated events are refused so one cannot outrank a real refusal recorded in
  the meantime.
- **Restore, never create** because the cheapest way to manufacture marketing consent is to let a
  keyword or an operator's lift stand in for one.

## What this rejects

- **Replacing `preferences` and `suppressions`.** They are live in production, read by code paths
  this change should not have to rewrite, and `suppressions` is keyed to outlive contacts. They are
  kept, honoured, and mirrored into rather than migrated away.
- **A per-contact `subscribed` boolean.** The spec's own objection: a flag has no history.
- **Inferring marketing consent from a bare START** or from an operator lifting a preference.
- **A dedicated unmask permission.** It would need a production role reconcile; the existing
  unmask permission and step-up are used and not weakened (see the open question below).

## Consequences

- **Rules with `category = 'marketing'` on email start requiring an opt-in**, where before they
  sent unless a preference said no. Check before deploying (see deploy notes).
- An email unsubscribe is **marketing-only**, in the ledger as it was in the legacy suppression,
  so transactional mail (invoices, receipts) to an unsubscribed address keeps flowing. A WhatsApp
  STOP still opts out of everything on that channel.
- Contacts written by a path that records no consent are not selected for campaigns.
- The ledger is retained, not erased: it is the evidence. Erasing a subject is crypto-shredding
  `subject_enc`, which this ADR does not build.
- The blind index inherits `pii_encryption_key` rotation's limitation: rotating it invalidates
  every stored index until rebuilt from the sealed values.

## Open questions for the owner

1. *(Decided 2026-10-12, see 6.)* An operator's add or import is not an opt-in; the add-contact
   form captures how the person agreed.
2. *(Decided.)* An email unsubscribe is marketing-only; a WhatsApp STOP is all purposes.
3. **Should an explicit, evidenced re-opt-in supersede an `unsubscribed` suppression?** Today it
   cannot: nothing that was blocked before may become allowed, and only `manual` suppressions are
   liftable. A re-subscribe form for email needs a decision and a lift path.
4. **A dedicated unmask permission** (`engage.audience.consent.unmask`) would describe what is
   being revealed better than `engage.inbox.message.unmask`, at the price of a production role
   reconcile.

## Revisit when

- WhatsApp marketing sends go live: the ledger gives the opt-in a home, but capturing one (a
  checkout tick-box, a web form, an inbound keyword programme) is a product surface not built here.
- Bounce and complaint processing is built: it should write `bounce` / `complaint` events itself.
