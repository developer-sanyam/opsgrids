# WhatsApp automation — Phase 1 audit

Written before any code, as the brief requires. It answers one question: what stands between the
WhatsApp plumbing that exists and a WhatsApp automation product.

The headline is not what the brief assumes. The brief treats template messages as the critical
gap (§4, "CRITICAL"), and they are a gap — but they are a **week of work against a known API**.
The thing that actually blocks WhatsApp automation to customers is four lines in a model file:

> `services/customer/app/models.py` — `phone` holds the **masked display form only**
> (`+91 98••• ••210`) — this read-only slice never accepts or stores a raw phone number.

**We cannot send WhatsApp to a customer because we do not have their phone number.** Not
encrypted, not restricted — absent. No amount of template-message code changes that, and every
downstream feature in the brief (§7 automation rules, §15 conversations, §16 inbox, §19
interactive replies, §38 conversation automation) inherits the blockage.

## 1. Current architecture

Thirty-three Python microservices behind two gateways, one PostgreSQL cluster with a schema per
service, RabbitMQ for domain events. Per-service NOBYPASSRLS database roles; row-level security
with the tenant GUC bound transaction-locally. Frontend is a pnpm workspace: a shared UI package,
an operator console, a customer client, a marketing site and nine product SPAs.

The event system is real and proven. Producers stage events into a per-service transactional
outbox in the same transaction as the state change; a relay process drains each outbox to a topic
exchange; consumers bind routing keys and mark `processed_events` inside the transaction that
holds their side effects. 91 routing keys are published across 12 products.

**There is no second event system to avoid building (§8) — this one is it, and notification is
already a consumer of all 91 keys.**

## 2. Existing WhatsApp components

Everything below is on `main` and tested. The brief's §41 instruction to preserve it is correct.

| Component | Where | State |
|---|---|---|
| Channel concept | `notification/app/domain.py` — `CHANNELS = ("email", "whatsapp")` | Done |
| E.164 normalisation | `domain.normalize_recipient` | Done. Strips human separators; **refuses** a number with no country code rather than guessing, because guessing sends a stranger someone else's invoice |
| Cloud API transport | `transports.WhatsAppCloudTransport` | Partial — text only, see §3 |
| Failure classification | `transports.TransportRefused` / `TransportUnavailable` | Done. 4xx permanent, 5xx transient, **429 transient** because time does fix rate limiting |
| Transport port | `transports.Transport` protocol + `build()` | Done. Refuses to start half-configured rather than silently degrading to the log transport |
| Rules engine | `services.apply_rules` | Done, channel-agnostic already |
| Strict rendering | `domain.render` | Done. An unresolved `{{placeholder}}` raises rather than sending "Dear {{name}}" |
| Message staging + lifecycle | `models.Message`, states `pending → sending → sent/failed/suppressed` | Partial — see §3 for the missing states |
| Dispatcher | `dispatcher.dispatch` | Done. Three transactions with the provider call between them and nothing open |
| Attempt ledger | `models.Delivery`, append-only | Done |
| Opt-out | `models.Preference`, checked at render **and** at dispatch | Done — the double check the brief asks for in §12 |
| De-duplication | `domain.dedupe_key` + unique `(organization_id, dedupe_key)` | Done — the brief's §11 idempotency, at a coarser grain |
| Tests | 105 passing in CI | Covers E.164, status mapping, rendering, suppression, dedupe, retry bounds, authz, cross-tenant isolation |

## 3. Missing components

Ordered by what blocks what, not by the brief's section order.

### Blockers that are not code

**B1. Customers have no phone number.** As above. `platform_core.pii` already provides
`seal()`/`mask()`, and `employee-service` already stores `personal_phone_enc`, so the pattern
exists — but `customer-service` must gain a write surface, `phone_enc` + `phone_masked` columns,
a migration, and a backfill path. Until then there is no customer to message.

**B2. No WhatsApp Business Account.** No WABA, no phone number, no access token, no App Secret.
Templates must be submitted to Meta and **approved by Meta** before they can be sent. That is
review latency measured in hours to days, outside our control, and it gates every end-to-end
test against the real API.

**B3. No sales-order domain.** The brief asks for order created/confirmed/packed/shipped/
delivered/cancelled events (§8). `operations` has `PurchaseOrder` — procurement, not sales.
There is no sales order, no fulfilment state machine, and therefore none of those events. This is
a product area, not an integration detail.

### Code gaps, in dependency order

| # | Gap | Notes |
|---|---|---|
| C1 | **Template messages** | Transport sends `type: "text"` only. Verified against current Meta docs: *"Template messages are the only type of message that can be sent to WhatsApp users outside of a customer service window."* This is why proactive automation does not work today |
| C2 | ~~API version is stale~~ | **Fixed in this change.** Pinned `v21.0`; v25.0 is current (released 2026-02-18). Left overridable per environment |
| C3 | **Template registry** | No model for Meta template name, language, category, variable definitions, approval status |
| C4 | **Variable mapping** | See the correction in §9 — smaller than the brief assumes |
| C5 | **24-hour window tracking** | Nothing records `last_customer_message_at`, because nothing records inbound messages at all |
| C6 | **Inbound webhook** | No endpoint. Needs `hub.challenge` verification and `X-Hub-Signature-256` HMAC-SHA256 over the **raw** body keyed by App Secret |
| C7 | **Webhook deduplication** | Meta redelivers. `processed_events` is the existing pattern to reuse |
| C8 | **Delivery/read receipts** | Lifecycle stops at `sent`. Needs `delivered`, `read`, and `queued`/`scheduled`/`cancelled` |
| C9 | **Conversations** | No conversation or inbound-message model |
| C10 | **Media** | Needs `document-service`, still a 187-line scaffold |
| C11 | **Interactive messages** | No buttons, no lists, no reply mapping |
| C12 | **Scheduling and delays** | `scheduler-service` is a 187-line scaffold. **This is why "invoice due in 3 days" is impossible today** — rules react to events, and no event describes time passing |
| C13 | **Per-attempt backoff** | Retry is bounded but flat; the brief wants 30s → 2m → 10m → 30m |
| C14 | **Loop protection** | Nothing prevents inbound → automation → outbound → event → automation |
| C15 | **Human handoff** | No assignment, no automation pause |
| C16 | **Conditions in rules** | A rule fires on an event type. No `amount > 5000` |
| C17 | **All UI** | No settings, template management, automation builder, inbox or analytics |

Also scaffolds, and needed: `activity` (timeline), `config` (per-tenant settings), `eventbridge`.
Five of the brief's dependencies are unbuilt services.

## 4. Database changes

Reuse before creating. The brief's §30 list is mostly right, with these corrections:

- **`WhatsAppOptOut` — do not create.** `notification.preferences` already does this, per channel
  and per category, with the double check enforced. Adding a WhatsApp-specific table would create
  a second source of truth for consent, which is the one place that must never disagree.
- **`WhatsAppMessage` — do not create.** `notification.messages` is already the outbound record
  and already carries the rendered text, provider message id and attempt ledger. Extend it with
  `direction` and the new states rather than forking it per channel.
- **`WhatsAppWebhookEvent` — reuse `processed_events`.** That is exactly the dedupe ledger.

Genuinely new, in the `notification` schema (channel-agnostic names where the concept is not
WhatsApp-specific):

| Table | Why |
|---|---|
| `provider_accounts` | WABA id, phone number id, sealed access token, sealed app secret, API version, per tenant |
| `message_templates` | Meta template name, language, category, status, variable definitions, component config |
| `conversations` | Customer, channel, `last_customer_message_at` (the 24-hour window), assignment, status |
| `inbound_messages` | Direction-in messages, media refs, interactive payloads |
| `automation_conditions` | The `amount > 5000` half of a rule |
| `scheduled_actions` | Send-after-delay and send-at-datetime, until `scheduler-service` exists |

Plus columns on existing tables: `messages.direction`, `messages.conversation_id`,
`messages.template_id`, and the four new states.

All tenant-scoped, all RLS-policied with `FORCE`, every index leading with `organization_id`.

## 5. API changes

- `POST /api/v1/notifications/webhooks/whatsapp` — **public** route (Meta cannot authenticate),
  therefore signature-validated, rate-limited, and registered in the gateway's `public_paths`.
- `GET` the same path — `hub.challenge` verification.
- Template CRUD + `POST .../templates:sync` against Meta.
- Conversation list/read/reply/assign/close.
- Provider account connect + a connection test (§28).
- Extend the existing rule endpoints with conditions and schedule.

The existing permission catalogue extends cleanly: `notification.delivery.*` gains
`conversation.read`, `conversation.write`, `template.sync`, `account.write`. `message.read` is
already marked dangerous because a rendered body carries the invoice total.

## 6. Frontend changes

New surface in `frontend/opsgrids-internal` (the operator console), reusing `@opsgrids/ui`:
settings + connection test, template management, automation builder, inbox, analytics, logs.

The inbox is the largest single piece of UI in the brief and needs realtime; there is no realtime
transport today (`eventbridge` is a scaffold), so v1 polls.

## 7. Job/queue changes

No new job system — the brief is right to forbid one, and the existing one suffices. Sending is
already driven by the outbox → relay → consumer path, which gives tenancy from the envelope and
retry from the broker.

Two additions: a `scheduler` capable of firing time-based actions (C12), and outbound rate
limiting per phone number to respect Meta throughput.

## 8. Webhook changes

New. Design constraints, all verified against current Meta documentation rather than memory:

1. `GET` with `hub.mode=subscribe`, compare `hub.verify_token`, echo `hub.challenge`.
2. `POST` — compute HMAC-SHA256 over the **raw request body** keyed by the App Secret, compare to
   `X-Hub-Signature-256` after the `sha256=` prefix, constant-time. **Read the raw body before
   any JSON parsing**, or the bytes signed and the bytes verified differ.
3. Dedupe on the Meta message/event id via `processed_events`.
4. Respond 200 fast; fold asynchronously. A slow fold makes Meta retry and widens the duplicate
   window.

## 9. A correction to my own earlier statement

I previously told the team that Meta templates take **positional** parameters and that this would
force an awkward mapping layer against our named `{{placeholder}}` rendering. **That was wrong,
or at least out of date.** Current Meta documentation describes both:

> Named parameters use descriptive labels wrapped in double brackets like `{{first_name}}`.
> Values can appear in any order during sending. Positional parameters use numbered
> placeholders (`{{1}}`, `{{2}}`) and require values to match the exact order.

So if templates are authored with **named** parameters, our existing renderer's vocabulary maps
across directly and C4 shrinks to validation — does this payload supply every variable this
template declares — rather than an ordering layer. The mapping table is still needed for
templates already approved with positional parameters, and for button parameters, which are
indexed by position regardless.

## 10. Testing plan

The existing suite is the model: pure logic tested without a database, integration tests against
real PostgreSQL as the NOBYPASSRLS app role so RLS is live, and no test ever opens a socket.

- **Unit** — template payload construction per component type; named and positional mapping;
  variable validation; window calculation at the boundary; signature validation including a
  tampered body and a wrong secret; status mapping; backoff schedule; loop-depth guard.
- **Integration** — `httpx.MockTransport` for every Meta response shape: approved template
  accepted, template rejected, outside-window refusal, 429, 5xx, invalid number. Webhook folds
  for inbound text, media, interactive reply, and each status event. Redelivery is a no-op.
  Cross-tenant: one tenant's webhook cannot touch another's conversation.
- **End-to-end** — the existing harness starts real services against real Postgres and RabbitMQ.
  Extend with a Meta stub: event → rule → condition → template → validation → idempotency →
  opt-out → queue → dispatcher → stub → provider id → webhook → delivered → read.
- **Never** real credentials in an automated test.

## 11. What the brief asks for that we should push back on

- **§22 cost analytics.** Meta pricing changed model in 2025 (per-message by category rather than
  per-conversation). The brief says not to fabricate pricing, which is right — so cost analytics
  needs a live rate card we do not have. Defer, and show message counts by category instead,
  which is the input to cost anyway.
- **§20 automation builder with waits and branches** is a workflow engine. Building one inside the
  notification service is how notification stops being a notification service. It belongs in
  `workflow`, and `scheduler` is its prerequisite.
- **§2 marketing messages.** Technically a template category; operationally a consent regime. The
  honest sequence is transactional/utility first, and marketing only once opt-in is recorded with
  provenance — who consented, when, to what.

## 12. Sequencing

Each phase ends shippable, and nothing depends on a later one.

| Phase | Content | Unblocks |
|---|---|---|
| **0** | Raw customer phone (B1): `phone_enc`/`phone_masked`, write surface, migration | Everything |
| **1** | Template messages (C1–C4) + API version bump (C2) + registry + validation | Proactive sending |
| **2** | Webhook (C6–C8): verification, signature, inbound, receipts, dedupe | Window tracking, real delivery truth |
| **3** | Conversations (C9) + window enforcement (C5) + STOP handling | Two-way messaging |
| **4** | Scheduler (C12) + conditions (C16) + backoff (C13) + loop guard (C14) | Time-based automation |
| **5** | Operator UI (C17): settings, templates, inbox | Non-developer operation |
| **6** | Media (C10), interactive (C11), handoff (C15), analytics | Completeness |

**Phase 0 and Phase 1 are the difference between a demo and a product.** Everything from Phase 3
on is a larger build than the notification service itself.

## Sources

Verified against Meta documentation on 2026-09-19 rather than from memory, per the brief's §43:

- [Template fundamentals](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview) — categories, named vs positional parameters, approval statuses, the customer-service-window rule
- [Cloud API messages reference](https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages/) — `POST /{Version}/{Phone-Number-ID}/messages`
- [Webhooks getting started](https://developers.facebook.com/docs/graph-api/webhooks/getting-started) — `hub.challenge` verification, `X-Hub-Signature-256`, HMAC-SHA256 over the raw payload keyed by App Secret

The exact template JSON schema page returned HTTP 500 at time of writing; the payload shape must
be confirmed against it before C1 is implemented rather than inferred from the summary pages.
