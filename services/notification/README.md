# Notification service

Part of the OpsGrids platform. Owns the `notification` schema.

```bash
make run SERVICE=notification PORT=8110    # API
make worker SERVICE=notification           # event consumer
make relay SERVICE=notification            # outbox relay
```

## Owns

- **Transactional plumbing** (`notification` product, gated on the `notification` entitlement):
  `templates`, `rules`, `messages`, the append-only `deliveries` ledger, `preferences`, and the
  WhatsApp inbox (`conversations`, `inbound_messages`).
- **Consent** (`consent_events`, both products): the append-only ledger of who opted in or out of
  what, and the one decision every send path asks — see "Consent" below.
- **Engage** (`engage` product, gated separately at `/api/v1/engage`): an organisation's own
  sending identity (`email_connections`, `email_domains`, `senders`), its audience (`contacts`,
  `contact_lists`, `suppressions`), and its `campaigns`. Two products, one process, two
  permission catalogues (`app/catalogue.py`) — see the module docstring on `app/api/engage.py`.

## Engage sending — the decision that shapes this module

**Customers send through their own email provider — Google Workspace, Microsoft 365, or their
own SMTP relay. OpsGrids publishes no mail infrastructure of its own.** There is no platform MTA,
no bounce envelope, and no link-rewriting proxy anywhere in the send path **by default**.
**Bounce processing is not built.** Open/click tracking exists only as a per-campaign opt-in (see
"Email tracking" below); a campaign that does not turn it on is sent exactly as written. Stating this plainly here because a prior version of
this module generated DNS instructions that pointed at OpsGrids-hosted records that never
existed — see `docs/superpowers/specs/2026-09-21-engage-email-platform-design.md` §7 for the
incident and the fix.

What that means concretely:

- `app/domain.py`'s `expected_records` derives the DNS a domain must publish from the
  organisation's *current* email connection (`google` | `microsoft` | `smtp`), computed fresh at
  every read and every `verify` — never frozen at the moment the domain was added. A domain with
  no connection yet has nothing to publish, and the setup screen says so.
- The record set is SPF and DKIM (DMARC recommended, never required, never blocking). No
  return-path record, no tracking record, no DKIM key this platform mints — each provider proves
  DKIM its own way, and this service checks for that provider's shape rather than a value only it
  could have generated.
- SPF is verified properly: one apex TXT record, containing the provider's include as a whole
  mechanism token. Zero records is `missing`; more than one is `multiple` (a domain may publish
  only one, and the service suggests the merge); one that lacks the include is `mismatch` (edit
  it, don't add a second record).
- `app/dns_check.py` is the one place this service resolves DNS — both the domain-verification
  reads (`check_domain_records`) and the outbound dial guard (`assert_dialable`) that keeps a
  customer-named SMTP host from pointing at this service's own compose network.
- The campaign send gate (`services.request_send`) still requires a verified domain, a tested
  sender and a connected provider before it stages a fan-out — it is checking against rules a
  customer can actually satisfy now.
- `POST /engage/campaigns/{id}/test` sends a real, rendered-exactly-as-a-recipient-would-see-it
  copy without requiring domain verification: a test never reaches a real recipient, so it proves
  the content and the connection, not the DNS.
- `GET /engage/setup` folds connection, domain, sender, contacts and campaign into one
  get-started checklist for a fresh organisation's first screen.

## Email tracking (opt-in per campaign)

`track_opens` and `track_clicks` on a campaign (both default **false**, settable while it is a
draft) rewrite that one campaign's links and add a 1x1 pixel at send time, through signed URLs on
the platform's own public API base (`ENGAGE_PUBLIC_API_BASE`): `GET /engage/t/o/{token}` (GIF) and
`GET /engage/t/c/{token}` (302). No customer DNS is involved. `app/e1_tracking.py` holds all of it.

- Tokens are `base64url(payload).base64url(mac)`: HMAC-SHA256 over the kind and the payload (org id,
  recipient id, link id, no address, name or URL), compared in constant time. The key derives from
  `ENGAGE_UNSUBSCRIBE_SECRET` unless `ENGAGE_TRACKING_SECRET` is set (at least 32 characters, and
  not a `dev-only-`/`ci-only-` placeholder outside local/test/ci); it is unavailable, and tracking
  is skipped with a log, wherever either key is refused. Rotating it stops recording on mail
  already sent.
- **No open redirect.** A click token names a row of `email_links`, registered from the anchors of
  the campaign body when it is sent. The redirect goes only to that row's `http(s)` URL; nothing the
  requester supplies becomes a `Location`. Unsubscribe links, `mailto:`, `tel:`, fragments and
  relative URLs are never rewritten, and the plain-text part is untouched.
- `email_events` is append-only (`UPDATE`/`DELETE` revoked from the app role) and stores the event,
  recipient, link, a device class and `occurred_at`. **No IP address and no raw user agent.** Scanner
  and prefetch traffic (known scanner agents, Apple Mail Privacy Protection, a fetch within seconds
  of the provider accepting the mail) is kept with `is_machine = true` and excluded from every
  human figure. The same event within five seconds is written once, and a recipient stops
  accumulating events of one kind after 200 (human and machine counted apart): past that the pixel
  and the redirect still answer but nothing is recorded, so the table cannot be grown without
  bound with one valid token.
- `GET /engage/campaigns/{id}/analytics` and `GET /engage/analytics/email/overview` read it
  (`app/e1_analytics.py`; `email_overview(session, organization_id, date_from, date_to)` is also
  what the hub embeds). Rates are fractions between 0 and 1. `delivered` means accepted by the
  provider. `complaints` is 0 because no complaint feedback loop is processed; `bounced` counts
  provider refusals at send time only.
- The test send (`POST /engage/campaigns/{id}/test`) is never tracked.

## Whose connection a message goes out on

The process-wide transports (`transports.build`: one SMTP relay, one WhatsApp number, from the
environment) belong to the **platform**. They carry **platform-owned** messages only — the
set-password and reset email auth publishes under the platform tenant (the nil UUID, ADR-0013
§3) — plus the `in_app` feed, which has no provider.

Everything else is **tenant-owned**, and `app/dispatcher.py` sends it through that organization's
own connection, resolved per send by `app/tenant_transport.py`:

- `whatsapp` — the org's `provider_accounts` row: its `phone_number_id` and its sealed token,
  opened for the send, on `WhatsAppCloudTransport`.
- `email` — the org's `email_connections` row, through the same builder campaigns and test sends
  use, so a tenant-named SMTP host passes the same egress guard (ADR-0016) on every send. It is
  sent as the connection's login when that is an address (Google Workspace, Microsoft 365),
  otherwise as the org's oldest verified sender.

**The marker is `messages.organization_id`.** It is fixed when the message is staged and bound by
row-level security, so a customer cannot mint a platform message and nothing about a rule, template
or category can promote one. The default is fail-closed: only the nil tenant reaches the shared
transport; a new customer-facing flow is safe without a line changed here.

**No connection, no send — and no fallback.** A tenant-owned message whose org has no usable
connection is failed at once (`state = failed`, never retried) with the reason in
`messages.last_error`, the failed `deliveries` row and the `message.failed` event, and one
structured log line (`dispatch.channel_unavailable`, with the organization id and reason, never a
credential):

| `last_error` | Meaning |
|---|---|
| `channel_not_connected` | No connected account for that org and channel (missing, disconnected, no token, or a `platform`-kind email connection). |
| `channel_credentials_unreadable` | The stored token or password cannot be opened with this environment's `PII_ENCRYPTION_KEY`. Reconnect the channel. |
| `smtp_host_not_allowed` | The org's SMTP host failed the egress guard on this send. |
| `sender_not_configured` | An SMTP relay whose login is not an address, and the org has no verified sender to send as. |

`EMAIL_TRANSPORT=log` is still honoured for tenant email, as it is for campaigns (the connection
is required either way). `WHATSAPP_TRANSPORT` selects only the platform's own sender: a connected
WhatsApp account is what enables that org's sending.

A transport is built per send and closed with it; nothing is cached, so a disconnected or replaced
credential stops being used on the next message. `email_connections.daily_cap` is enforced for
campaigns only (`claim_campaign_batch`, which already counts rule-driven mail against it); a
rule-driven send is not refused at the cap.

## Consent

Consent used to be split across two stores that never looked at each other: `preferences` (opt-outs
for rule-driven sends, "absence means send") and `suppressions` (email, checked only by campaigns).
Neither recorded **who** agreed, **when**, or **through what** — and Meta requires an opt-in for
business-initiated WhatsApp messages. `consent_events` is the record, and `app/consent.py`'s
`decide(session, organization_id, channel, purpose, address)` is the one question every send path
now asks. Design: [ADR-0021](../../docs/adr/0021-consent-ledger.md).

**The ledger.** One row per fact: `channel` (`whatsapp | email | sms`), `purpose` (`marketing |
transactional`), the subject, `status` (`opted_in | opted_out`), `source`, `evidence`,
`recorded_by`, `occurred_at`. **Append-only, enforced by the database**: `UPDATE` and `DELETE` are
revoked from the app role (`migrations/env.py` re-applies the revoke after every schema-grant run),
so a change of mind is a new row. The subject is the address **sealed** with a blind index — the
same index `contacts`, `suppressions` and `conversations` use — and there is no plaintext address
anywhere in the table. Reads return it masked; `POST /engage/consent/events/{id}:unmask` opens it
behind the existing `engage.inbox.message.unmask` permission, an OTP step-up, and an audit row
every time.

**The policy.** The latest event for `(organization, channel, purpose, subject)` wins, ordered by
`(occurred_at, created_at, id)`:

| Purpose | Allowed when | Refused when |
|---|---|---|
| `transactional` | no event, or the latest is `opted_in` | the latest is `opted_out`; for email, also a `bounced` or `complained` suppression |
| `marketing` | the latest is `opted_in` | the latest is `opted_out` **or there is no event at all** — silence is not consent |

A STOP keyword opts the subject out of **both** purposes on that channel; an email unsubscribe link opts the address out of **marketing only** (invoices and receipts keep flowing).
A rule is `marketing` when its `category` is `marketing` **or** the provider template it sends is
registered as `marketing` (Meta's own classification, which is what carries the obligation);
everything else is `transactional`. `in_app` has no consent regime.

**The legacy stores still count.** `decide` also refuses when a `Preference` or a `Suppression`
says no — deny if *either* says no — so nothing blocked before the ledger became allowed by it. The
writers keep them in step: STOP/START, the unsubscribe link, `set_preference`,
`add_suppression`/`remove_suppression` all write the ledger too. Lifting a refusal (START, an
operator lifting a preference or a manual suppression) **restores consent that existed and never
creates it**: transactional is re-opened, marketing only if the subject had opted in before.

**Where it is called.** Rule staging (`stage_message`) and dispatch (`claim_for_send`) call
`consent.check_send`; a refusal marks the message `suppressed` with a machine-readable
`Message.suppression_reason` (`consent:no_opt_in`, `consent:opted_out`, ...). Campaigns read
`selectors.sendable_contacts`, whose SQL predicate is pinned to `decide` by a test, and
`claim_campaign_batch` asks again per batch (`consent.decide_indexes`, two queries for the batch)
and records the reason on the recipient's `error` (`consent:opted_out`). The inbound STOP/START
handler and both unsubscribe POSTs (one-click and the confirm form) write the ledger.

**`legacy_import` is unverified consent — read this before relying on the ledger as evidence.**
When the ledger was introduced, every existing active, unsuppressed contact was carried in as an
`opted_in` email/marketing event with `source = legacy_import` so existing campaigns kept selecting
the same people. Nothing records how or when they agreed; the evidence text says so. Likewise
**contacts added or imported through the API are recorded as the organization's *attestation***
(`source = admin` / `import`, attributed to the operator, evidence "attests ... (unverified)") —
and only where the ledger holds no earlier marketing event for that address, so re-importing
somebody never undoes their opt-out. Filter on `source` to tell a customer's own opt-in
(`checkout`, `web_form`, `inbound_keyword`) from these. **WhatsApp marketing has no such carry-over**:
there were no legacy WhatsApp marketing sends, so it needs an explicit `opted_in` event.

**Backfill.** The migration (`d5a7c3e91f48`) runs `app/consent_backfill.py`: every `Suppression`
becomes an `opted_out` event (`unsubscribed` → `unsubscribe_link`, `manual` → `admin`, `bounced` →
`bounce`, `complained` → `complaint`; an unsubscribe or manual suppression opts out of marketing
only, exactly what it did before, and a bounce or complaint of both), every `Preference` refusal
becomes `opted_out` events for the purposes its category covers, and every active unsuppressed
contact becomes the `legacy_import` opt-in above. It is idempotent (a deterministic
`idempotency_key` per legacy row) and can be re-run at any time as the admin role:
`cd services/notification && uv run python -m app.consent_backfill` — needed once if the previous
release created contacts between the migration and the new code taking over.

**API** (all under `/engage`, Engage entitlement, **no new permission codes**):

| Route | Permission | |
|---|---|---|
| `POST /consent/events` | `engage.audience.contact.write` | Record an event. `source` (`checkout`, `web_form`, `import`, `admin`, `api`) and `evidence` are required; the system sources cannot be claimed. By `address` or, for email, `contact_id`. Optional `occurred_at` (not in the future) and `idempotency_key`. Returns the event (masked) and `effective`, the decision now. 201, or 200 on an idempotent replay. |
| `GET /consent/events` | `engage.audience.contact.read` | The ledger, newest first, paged; filters `channel`, `purpose`, `status`, `contact_id`. |
| `POST /consent/events:lookup` | `engage.audience.contact.read` | One subject's history by address, in the body so it never reaches an access log. |
| `POST /consent/events/{id}:unmask` | `engage.inbox.message.unmask` + `amr=otp` | Open one event's address. Audited every time. |

An opt-in can be recorded for an address a legacy suppression or a customer's STOP still refuses;
`effective` says so (`suppressed_unsubscribed`, `preference_suppressed`, ...) rather than the
caller assuming it took effect.

## WhatsApp Embedded Signup, numbers and health (`app/w1_*.py`)

`POST /engage/whatsapp/signup/complete` exchanges the popup's one-time code for a token **inside the
request** (Meta's code lives about 30 seconds, so it cannot wait behind a queue), seals the token on a
`w1_setup_runs` row (the code itself is never stored), stages `notification.whatsapp.setup_requested`
through the outbox and answers 202. After commit the worker (`consumers.run_whatsapp_setup` →
`app/w1_setup.py`) runs six steps, each committed before the next: `token` (already done by the
request), `number` (discover the WABA, prove ownership, store the sealed token in
`provider_accounts`, fill the numbers cache), `webhook` (`POST /{waba}/subscribed_apps`), `register`
(`POST /{phone_number_id}/register` with a random six-digit PIN, sealed on `w1_numbers`; skipped when
Meta already reports the number `CONNECTED`, counted against Meta's ten calls per 72 hours, never
retried blindly), `templates` (`w2_services.sync_templates`), `verify` (read the number and
subscription back; fails with `number_not_connected` when the number still cannot send). A refused
code answers 409 `code_expired` / `code_exchange_failed` (503 `meta_unreachable`, retryable with the
same code). A failing step fails the run with a machine-readable `error_code`
(`ownership_check_failed`, `number_claimed`, `pin_required`, `register_rate_limited`,
`webhook_subscribe_failed`, `templates_sync_failed`, `webhook_not_subscribed`, `meta_unreachable`,
...). The token and the PIN are sealed at rest, and never appear in a response, an event, an audit
row or a log line. `POST /engage/whatsapp/disconnect` releases the routing key
(`provider_accounts.phone_number_id` becomes null, the number is kept in
`released_phone_number_id`), so the number can be connected by another organization and a webhook for
it is dropped as an unknown number.

| Route | Permission | Notes |
|---|---|---|
| `GET /engage/whatsapp/signup/config` | `engage.setup.whatsapp.read` | `enabled: false` unless `META_APP_ID`, `META_APP_SECRET` and `META_EMBEDDED_SIGNUP_CONFIG_ID` are set. |
| `POST /engage/whatsapp/signup/complete` | `engage.setup.whatsapp.write` | 202 once the code is exchanged; idempotent on the code; 409 `setup_in_progress` / `code_expired` / `code_exchange_failed`, 503 `meta_unreachable`. |
| `GET /engage/whatsapp/signup/{id}` | `engage.setup.whatsapp.read` | Steps and state; another organization's id is a 404. |
| `GET /engage/whatsapp/numbers` | `engage.setup.whatsapp.read` | Cached; refreshed from Graph when older than ten minutes. |
| `POST /engage/whatsapp/numbers/{id}/default` | `engage.setup.whatsapp.write` | Moves the webhook routing key, so the number must be on the account's own WABA and Meta must confirm (with the connection's token) that it controls it; mirrors it into `provider_accounts`. 409 `number_inactive` / `ownership_check_failed` / `number_claimed`. |
| `GET /engage/whatsapp/health` | `engage.setup.whatsapp.read` | From our own tables; never calls Meta. |
| `POST /engage/whatsapp/test-message` | `engage.setup.whatsapp.write` | Staged through the consent decision, classified by the synced template's own category (a marketing template needs an opt-in). 404 `template_not_found`; 409 `channel_not_connected` / `consent_denied`. |
| `POST /engage/whatsapp/disconnect` | `engage.setup.whatsapp.write` | Wipes the sealed token; history is kept. After the commit the app is unsubscribed from the WABA (`DELETE /{waba}/subscribed_apps`), unless another organization still uses that WABA; a failure there does not fail the disconnect and is reported in `detail`. |

A signup on a different WABA than the connection it replaces marks every cached number of the old
WABA `disconnected` and, best effort, unsubscribes the app from it. At most one setup runs per
organization: a partial unique index on `w1_setup_runs` enforces what `signup/complete`'s read
checks.

`phone_number_quality_update` webhooks reach the numbers cache through
`w2_webhooks.register_number_quality_handler` (registered in `app/api/w1_signup.py`).

## Publishes

Transactional: `notification.delivery.message.{queued,sent,failed,delivered,read}`,
`notification.whatsapp.inbound_received`. Internal to this service:
`notification.whatsapp.setup_requested` (a WhatsApp signup is ready to run).

Engage sending identity: `engage.setup.connection.{created,replaced}`,
`engage.setup.whatsapp.{created,replaced}`, `engage.setup.domain.{created,verified}`,
`engage.setup.sender.{created,verified}`. Engage campaigns:
`engage.campaign.send_requested`, `engage.campaign.cancelled`. Scheduled through the scheduler
(ADR-0015), and so namespaced `notification.` because it only delivers a requester's own events:
`notification.campaign.send_due` (a scheduled send is due), `notification.campaign.ab_decide_due`.

## Consumes

Every routing key named in `app/domain.py`'s `SUBSCRIBABLE_EVENTS` — the platform-wide list of
events a `Rule` may bind a notification to. Adding a producer means adding its key there first;
an unbound event type is refused at rule-authoring time rather than silently never firing.
