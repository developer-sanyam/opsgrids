# Engage — organisation communication platform

**Status:** design, approved 2026-09-21
**Supersedes nothing.** Extends `notification-service`.

Engage is the customer-facing communication module: configure once, manage contacts, build
campaigns, automate, track, handle replies, audit. It is deliberately *not* positioned as "bulk
email" — email is the first channel on an architecture that already carries WhatsApp.

---

## 1. The three decisions everything else rests on

### 1.1 Engage is built **inside `notification-service`**, not as a new service

The tempting shape is a new `engage-service` owning campaigns and lists, delegating sends to
notification. It does not survive contact with the platform's rules.

Campaign analytics — delivered, opened, clicked, bounced, unsubscribed — are derived from
`notification.messages` and the append-only `notification.deliveries` ledger. **No service may read
another's tables** (CLAUDE.md), and there are no cross-schema foreign keys. A separate engage
service could only get those numbers by polling an HTTP endpoint per campaign per metric, or by
duplicating the ledger. Both are worse than one larger schema.

So the `notification` schema grows. It already holds `templates`, `provider_templates`, `rules`,
`provider_accounts`, `messages`, `deliveries`, `preferences`. Engage adds sending identity,
audience, campaign and consent tables alongside them. Everything Engage reports on is a local join.

**Two product surfaces, one service.** `notification` stays platform plumbing (transactional
rules, the existing message API). `engage` is a new **sellable product key** with its own
permission catalogue, gated at the gateway with `"product": "engage"`. One service can serve two
product surfaces; a product is a permission catalogue and an entitlement, not a process.

### 1.2 Contacts are a **projection**, not a second database

The spec is explicit: do not create a separate email contact database, connect to CRM. The
platform forbids reading `customer.contacts` directly.

The established answer is already used twice in this repo — `approval-service` keeps a read-only
copy of the reporting hierarchy fed by employee's events, and `console-service` folds subscription,
invoice, credit and usage events into its own rollups. Engage does the same:

- **Backfill:** a keyset-paged `/internal/v1` export on **customer-service**, called under a
  short-lived service token. This endpoint **does not exist yet and must be built** — customer's
  only contact-resolving internal route explicitly refuses email
  (`_PHONE_CHANNELS = frozenset({"whatsapp", "sms"})`, `services/customer/app/api/internal.py:60`).
- **Steady state:** engage consumes `crm.book.contact.*` events and folds them into its projection,
  gated on a monotonic version so a late or duplicate event cannot regress a row.

The projection carries only what sending needs: address, display name, merge fields, and the
identifiers needed to link back (`contact_id`, `company_id`). Customer-service stays the system of
record. Customer 360 (§19 of the spec) is assembled at read time in the UI from each product's own
API, not by one service hoarding another's data.

### 1.3 Organisations bring their **own** sending identity

Chosen explicitly: each organisation configures its own provider, and the platform sends through
it. This is not only a product choice, it removes a real risk — a single platform IP sending every
tenant's marketing makes one tenant's spam complaints everyone's deliverability problem.

`provider_accounts` models exactly this shape for WhatsApp: per-org, per-channel, credential sealed
through `platform_core.pii.seal`.

**Engage does not extend that table, it copies the pattern into a new `email_connection` table.**
The first draft of this design said extend it; reading the schema says otherwise. `waba_id` and
`phone_number_id` are `NOT NULL`, and the table's only unique constraint is
`uq_provider_accounts_phone_number_id` — a *global* uniqueness deliberately chosen because that
column is Meta's webhook routing key. Email has no equivalent, needs none of those columns, and
needs several the table lacks (connection kind, host, port, TLS, username, daily cap). Reusing it
would mean making the routing key nullable and adding channel-conditional CHECK constraints, which
weakens a constraint that is load-bearing for WhatsApp in order to borrow three columns.

`email_connection` therefore carries: `kind` (`google | microsoft | smtp | platform`), the derived
transport settings, `secret_enc` sealed the same way, `status`, and `verified_at` — which only a
successful test send sets.

**The UX must not expose SMTP/DNS vocabulary** (spec §21). The wizard asks *how would you like to
send email* — Google Workspace, Microsoft 365, custom SMTP, or platform-managed — and the system
derives host, port, TLS and auth from that choice.

---

## 2. What already exists and is reused unchanged

| Capability | Where | Note |
|---|---|---|
| Message lifecycle | `notification/app/domain.py:26-32` | `pending → sending → sent → delivered → read`, plus terminal `failed`/`suppressed`. Engage adds no new states. |
| Append-only delivery ledger | `notification.deliveries` | One row per provider attempt. Never updated. The analytics substrate. |
| Suppression | `notification.preferences` | Per recipient, per channel, per category. Absence means send. |
| Template rendering | `domain.render` | Strict — an unresolved `{{placeholder}}` raises. |
| Send pipeline | `stage_message` → outbox → relay → dispatcher → transport | Event-driven; nothing polls. |
| Per-org sealed credentials | `notification.provider_accounts` | Built for WhatsApp; extended here. **Currently has no CRUD route.** |
| Approval workflow | `approval-service`, port 8152 | Reused for campaign approval (spec §17) rather than rebuilt. |
| Audit | `platform_core.audit` | Every write already lands an audit row. |

## 3. What must be built

Grouped by the slice that delivers it. Each slice is independently shippable.

### Slice 1 — Sending identity (foundation)
Nothing can send until an organisation has a verified way to send.
- `email_domain` — domain, DNS record set (SPF/DKIM/DMARC/return-path/tracking), per-record
  verification state and last-checked time.
- `sender` — name, address, reply-to, signature, department, domain, default template.
- Extend `provider_accounts` to `channel="email"`, sealed credentials, connection kind.
- CRUD routes for all three (provider accounts have none today).
- **Send test message** — the gate. A sender is not usable until a test send succeeds.
- DNS verification by real lookup, re-checked on a schedule.

### Slice 2 — Audience
- `contact` projection + backfill endpoint on customer-service + event consumer.
- `contact_list` / `list_member`, CSV import.
- `segment` — a stored predicate over projection columns, resolved at send time. Dynamic by
  construction because the projection is live.
- `consent_event` — append-only. Consent is a history with source, timestamp and IP, never a
  boolean that gets flipped (spec §13).
- `suppression` — unsubscribed, hard bounce, complaint, invalid, blocked, manual. Distinct from
  `preferences`: a preference is a person's choice, a suppression is a deliverability fact.

### Slice 3 — Campaign and the bulk engine
- `campaign`, `campaign_recipient`.
- Audience resolution → consent check → suppression check → dedupe → fan-out.
- **Chunked fan-out.** 50k recipients is 50k message rows and 50k outbox events; the single
  `edge-relay` container runs in 256 MB. Fan-out runs in batches from `bin/cron`, never in a
  request, and never in one transaction.
- Per-organisation throttle matched to that tenant's provider limits, plus daily/hourly caps and
  an approval threshold above N recipients (spec §18).
- **Unsubscribe** — signed token, anonymous public route, `List-Unsubscribe` header. Legally
  required, and there are currently **zero occurrences of "unsubscribe" in the repository**.
- **Fix the dead retry path** (§4 below) — a bulk sender without retry is not a bulk sender.

### Slice 4 — Feedback and analytics
- Provider webhooks per tenant: delivered, bounced, complained, opened, clicked.
- Hard bounce and complaint auto-suppress.
- Webhook dedupe and a dead-letter path.
- Campaign → recipient → event timeline.

### Later phases (spec §23, §24)
Automation builder, shared inbox and reply handling, A/B testing, AI campaign creation, and the
unified WhatsApp/SMS communication centre. Out of scope for this design beyond ensuring nothing
here blocks them — which is why the module is named Engage and not Email.

---

## 4. A defect this design must fix first

Transient send failures never retry. `record_failure` returns the message to `pending`
(`services/notification/app/services.py:872`), but `EVT_MESSAGE_QUEUED` is published in exactly one
place — `stage_message` (`services.py:426`) — and the dispatcher has already committed
`mark_processed` in its claim transaction (`dispatcher.py:68`). Nothing re-queues the row, so
`dispatch_max_attempts = 6` yields exactly **one** real attempt.

This is invisible at one-message-at-a-time volumes and unacceptable at campaign volumes.

## 5. Constraints carried from the platform

Not style preferences — these cause data corruption or audit failure:

- Every table carries `organization_id`; every query filters on it. RLS is the net, not the plan.
- The tenant GUC is transaction-local. Per-connection is a cross-tenant leak.
- Consumers and relays need `BYPASSRLS`; getting it wrong stalls event processing **silently**.
- No cross-schema foreign keys. `contact_id` in the projection is a plain indexed UUID.
- Enqueue work only after commit, through the outbox — never inline.
- Money in integer minor units. Consent, suppression and delivery ledgers are append-only;
  corrections are new rows.
- Authorization is server-side; every list endpoint injects scope and every endpoint carries a
  negative-authz test.
- PII is masked by default. **Employee `personal_email` is sealed and must never feed a campaign** —
  it is a personal address behind a stepped-up unmask. Contact and user emails are plaintext and
  are the legitimate sources.

## 6. Deliverability reality

The committed production intent is Google Workspace submission (`smtp.gmail.com:587`, an app
password). That is a mailbox, not an MTA — Google caps external recipients near 2,000/day, so a
50k campaign would suspend the account rather than send slowly. Per-tenant configuration is what
makes this workable: each organisation brings its own provider and its own reputation, and the
platform enforces a throttle derived from the connection kind they chose.

Production today sends nothing at all: `EMAIL_TRANSPORT` defaults to `log` in the service, in
compose, and in the edge env file, and preflight warns "NO EMAIL IS SENT" in capitals. Slice 1's
test-send gate is what turns that from a silent default into a visible, per-organisation state.
